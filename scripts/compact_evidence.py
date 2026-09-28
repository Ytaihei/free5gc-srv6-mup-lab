"""Guest-only packet/BPF evidence, kept outside the public source inventory."""
from __future__ import annotations

import json
import ipaddress
from pathlib import Path
import signal
import subprocess
import time
import struct

from compact_vm import output


def container_pid(service):
    # Fixed project and allowlisted roles only; never accept a raw container ID.
    from compact_compose import CORE_SERVICES, ROLE_SERVICES
    if service not in CORE_SERVICES + ROLE_SERVICES:
        raise ValueError('unknown capture service')
    pid = int(output(['docker', 'inspect', '--format', '{{.State.Pid}}',
                      'srv6-mup-compact-' + service + '-1']))
    if pid <= 0:
        raise ValueError(f'{service} is not running')
    return pid


class Capture:
    """Capture only selected lab namespaces, not guest management/Tailnet."""
    POINTS = {'n3': ('ran', 'n3'), 'sr': (None, 'sr'),
              'n6': ('dn', 'n6'), 'upf-n3': ('free5gc-upf', 'n3b'),
              'upf-n6': ('free5gc-upf', 'n6')}
    CONTROL_POINTS = {'n2': ('ran', 'n2'), 'n4': (None, 'privnet'), 'bgp': (None, 'mgmt')}

    def __init__(self, directory, control=False):
        self.directory = Path(directory)
        self.processes = []
        self.points = self.CONTROL_POINTS if control else self.POINTS

    def __enter__(self):
        self.directory.mkdir(parents=True, mode=0o700)
        try:
            for name, (service, interface) in self.points.items():
                log = (self.directory / (name + '.log')).open('w')
                prefix = ['nsenter', '-t', str(container_pid(service)), '-n'] if service else []
                if not service:
                    network = json.loads(output(['docker', 'network', 'inspect', 'srv6-mup-compact_' + interface]))[0]
                    interface = network['Options'].get('com.docker.network.bridge.name', 'br-' + network['Id'][:12])
                process = subprocess.Popen([*prefix, 'tcpdump', '--immediate-mode', '-nn', '-U', '-s', '0', '-Z', 'root', '-i', interface,
                    '-w', str(self.directory / (name + '.pcap')),
                    *({'n2': ['sctp'], 'n4': ['udp', 'port', '8805'], 'bgp': ['tcp', 'port', '179']}.get(name, []))],
                    stdout=subprocess.DEVNULL, stderr=log)
                self.processes.append((process, log))
            end = time.monotonic() + 5
            while time.monotonic() < end:
                if any(p.poll() is not None for p, _ in self.processes):
                    raise ValueError('capture exited during startup; inspect private evidence logs')
                if all('listening on' in Path(log.name).read_text() for _, log in self.processes):
                    return self
                time.sleep(0.1)
            raise ValueError('capture startup timed out')
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        failures = []
        for process, _ in self.processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process, log in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            finally:
                log.close()
            if process.returncode != 0 or '0 packets dropped by kernel' not in Path(log.name).read_text():
                failures.append(Path(log.name).name)
        if failures and not args[0]:
            raise ValueError('incomplete packet capture: ' + ', '.join(failures))


def bpf_state(execute):
    """Resolve maps from attached programs and tail-call graph, never by name alone."""
    all_maps = {item['id']: item for item in json.loads(output(['bpftool', '-j', 'map', 'show']))}
    all_programs = {item['id']: item for item in json.loads(output(['bpftool', '-j', 'prog', 'show']))}
    result = {}
    for role, interface in (('tpe', 'n3'), ('npe', 'n6')):
        link = json.loads(execute(role, 'ip', '-j', '-d', 'link', 'show', interface))[0]
        xdp = link.get('xdp', {})
        if xdp.get('mode') != 2:
            raise ValueError(f'{role} is not using generic XDP')
        pending, programs, maps = [xdp['prog']['id']], set(), set()
        while pending:
            program = pending.pop()
            if program in programs:
                continue
            programs.add(program)
            for map_id in all_programs[program].get('map_ids', []):
                if map_id in maps:
                    continue
                maps.add(map_id)
                if all_maps[map_id]['type'] == 'prog_array':
                    entries = json.loads(output(['bpftool', '-j', 'map', 'dump', 'id', str(map_id)]))
                    for entry in entries:
                        value = entry.get('value')
                        if isinstance(value, int):
                            pending.append(value)
                        elif isinstance(value, list):
                            pending.append(int.from_bytes(bytes(int(b, 16) for b in value), 'little'))
                        else:
                            raise ValueError('unsupported bpftool program array encoding')
        selected = [all_maps[key] for key in sorted(maps)
                    if all_maps[key]['name'] in ('mup_uplink_v4_m', 'headend_v4_map', 'stats_map')]
        result[role] = {'interface': interface, 'xdp': xdp, 'program_ids': sorted(programs),
                        'maps': [{**item, 'entries': json.loads(output([
                            'bpftool', '-j', 'map', 'dump', 'id', str(item['id'])]))} for item in selected]}
    return result


def records(path):
    raw = Path(path).read_bytes()
    if len(raw) < 24 or raw[:4] not in (b'\xd4\xc3\xb2\xa1', b'\xa1\xb2\xc3\xd4'):
        raise ValueError('expected classic microsecond pcap')
    endian = '<' if raw[0] == 0xd4 else '>'
    if struct.unpack_from(endian + 'I', raw, 20)[0] != 1:
        raise ValueError('expected Ethernet packet evidence')
    cursor = 24
    while cursor < len(raw):
        if len(raw) - cursor < 16:
            raise ValueError('truncated pcap record header')
        captured, original = struct.unpack_from(endian + 'II', raw, cursor + 8)
        cursor += 16
        packet = raw[cursor:cursor + captured]
        if len(packet) != captured or captured != original:
            raise ValueError('truncated pcap packet')
        cursor += captured
        yield packet


def ipv4(packet, encapsulation=None):
    if len(packet) < 20 or packet[0] >> 4 != 4:
        raise ValueError('invalid inner IPv4 packet')
    header = (packet[0] & 15) * 4
    length = int.from_bytes(packet[2:4], 'big')
    if header < 20 or length < header or length > len(packet):
        raise ValueError('truncated IPv4 payload')
    data = {'src': str(ipaddress.ip_address(packet[12:16])),
            'dst': str(ipaddress.ip_address(packet[16:20])),
            'protocol': packet[9], 'encapsulation': encapsulation}
    payload = packet[header:length]
    if packet[9] == 1 and len(payload) >= 8 and payload[0] in (0, 8):
        data['echo'] = (data['src'], data['dst'], *struct.unpack_from('!HH', payload, 4))
    if packet[9] == 6:
        pseudo = packet[12:20] + bytes([0, 6]) + len(payload).to_bytes(2, 'big')
        checksum_data = pseudo + payload + (b'\0' if len(payload) % 2 else b'')
        total = sum(struct.unpack('!' + 'H' * (len(checksum_data) // 2), checksum_data))
        while total >> 16:
            total = (total & 65535) + (total >> 16)
        data['tcp_checksum_valid'] = total == 65535
    return data


def decode(packet):
    if len(packet) < 14:
        raise ValueError('truncated Ethernet header')
    ether = int.from_bytes(packet[12:14], 'big')
    payload = packet[14:]
    if ether == 0x86dd:
        if len(payload) < 40:
            raise ValueError('truncated IPv6 packet')
        next_header, cursor = payload[6], 40
        # Bounded SRH/hop/destination-options traversal, no fragment guessing.
        for _ in range(8):
            if next_header not in (0, 43, 60):
                break
            if len(payload) < cursor + 8:
                raise ValueError('truncated IPv6 extension')
            next_header, size = payload[cursor], (payload[cursor + 1] + 1) * 8
            cursor += size
        return ipv4(payload[cursor:], 'srv6') if next_header == 4 else None
    if ether != 0x0800:
        return None
    parsed = ipv4(payload)
    offset = (payload[0] & 15) * 4
    if parsed['protocol'] != 17 or len(payload) < offset + 16:
        return parsed
    if 2152 not in struct.unpack_from('!HH', payload, offset):
        return parsed
    gtp = payload[offset + 8:]
    if gtp[0] >> 5 != 1 or gtp[1] != 255:
        return None
    cursor = 8
    if gtp[0] & 7:
        if len(gtp) < 12:
            raise ValueError('truncated GTP optional header')
        cursor, extension = 12, gtp[11] if gtp[0] & 4 else 0
        for _ in range(8):
            if not extension:
                break
            if cursor >= len(gtp) or not gtp[cursor]:
                raise ValueError('invalid GTP extension')
            size = gtp[cursor] * 4
            if cursor + size > len(gtp):
                raise ValueError('truncated GTP extension')
            extension = gtp[cursor + size - 1]
            cursor += size
        if extension:
            raise ValueError('too many GTP extensions')
    inner = ipv4(gtp[cursor:], 'gtpu')
    inner['teid'] = int.from_bytes(gtp[4:8], 'big')
    return inner


def verify_packets(directory, session, dn, mup):
    """Correlate actual echo identities across N3/SRv6/N6 and exclude UPF use."""
    flows = {}
    for point in Capture.POINTS:
        packets = [decode(packet) for packet in records(Path(directory) / (point + '.pcap'))]
        flows[point] = [p for p in packets if p and {p['src'], p['dst']} == {session['ue_ipv4'], dn}]
    echoes = {point: {p['echo'] for p in packets if 'echo' in p} for point, packets in flows.items()}
    if len(echoes['n3']) < 16 or not echoes['n3'] <= echoes['n6']:
        raise ValueError('not all 8 echo requests/replies observed on both N3 and N6')
    for packet in flows['n3']:
        expected = session['uplink_teid'] if packet['src'] == session['ue_ipv4'] else session['downlink_teid']
        if packet.get('encapsulation') != 'gtpu' or packet.get('teid') != expected:
            raise ValueError('N3 GTP-U TEID does not match the observed PFCP session')
    if mup:
        if not echoes['n3'] <= echoes['sr']:
            raise ValueError('SRv6 capture is missing test echo packets')
        if flows['upf-n3'] or flows['upf-n6']:
            raise ValueError('MUP user traffic reached UPF: bypass not proven')
        tcp = [p for p in flows['n3'] if p['protocol'] == 6]
        if not tcp or not all(p['tcp_checksum_valid'] for p in tcp):
            raise ValueError('MUP TCP checksum validation failed')
    else:
        if flows['sr'] or not echoes['n3'] <= echoes['upf-n3'] or not echoes['n3'] <= echoes['upf-n6']:
            raise ValueError('ordinary UPF baseline not proven')
    return {'mode': 'mup' if mup else 'baseline', 'ue': session['ue_ipv4'],
            'packet_counts': {point: len(items) for point, items in flows.items()},
            'echo_counts': {point: len(items) for point, items in echoes.items()},
            'pfcp_teids_match': True, 'path_verified': True}


def verify_control(directory):
    pfcp_types, ngap, bgp_updates = set(), 0, 0
    for point in Capture.CONTROL_POINTS:
        for packet in records(Path(directory) / (point + '.pcap')):
            if len(packet) < 34 or packet[12:14] != b'\x08\x00':
                continue
            ip = packet[14:]
            offset = (ip[0] & 15) * 4
            payload = ip[offset:int.from_bytes(ip[2:4], 'big')]
            if point == 'n4' and ip[9] == 17 and len(payload) >= 16:
                pfcp_types.add(payload[9])
            elif point == 'n2' and ip[9] == 132:
                ngap += 1
            elif point == 'bgp' and ip[9] == 6 and len(payload) >= 20:
                data = payload[(payload[12] >> 4) * 4:]
                marker = data.find(b'\xff' * 16)
                if marker >= 0 and len(data) >= marker + 19 and data[marker + 18] == 2:
                    bgp_updates += 1
    if not {50, 51, 52, 53, 54, 55} <= pfcp_types or not ngap or not bgp_updates:
        raise ValueError(f'1call control evidence incomplete: PFCP={pfcp_types}, SCTP={ngap}, BGP UPDATE={bgp_updates}')
    return {'pfcp_message_types': sorted(pfcp_types), 'n2_sctp_packets': ngap,
            'bgp_update_packets': bgp_updates, 'control_verified': True}
