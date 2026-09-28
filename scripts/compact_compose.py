"""Render one Compose topology from the existing reviewed logical lab model."""
from __future__ import annotations

import copy
import hashlib
import re
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

from compact_config import ROOT, locks, module
from compact_vm import output, write

CORE_SERVICES = ['free5gc-upf', 'db', 'free5gc-nrf', 'free5gc-amf', 'free5gc-ausf',
                 'free5gc-nssf', 'free5gc-pcf', 'free5gc-smf', 'free5gc-udm',
                 'free5gc-udr', 'free5gc-webui', 'free5gc-chf']
ROLE_SERVICES = ['mupc', 'observer', 'tpe', 'npe', 'ran', 'ue', 'dn']
EXTRA_SERVICES = ['dashboard']


def candidate_nf_images(candidate):
    """Legacy candidates remain usable; a new image set must be complete."""
    if 'image_set_schema' not in candidate and 'nf_images' not in candidate:
        return {}
    images = candidate.get('nf_images')
    if (candidate.get('image_set_schema') != 1 or not isinstance(images, dict)
            or set(images) != set(CORE_SERVICES) - {'db'}
            or any(not isinstance(value, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', value)
                   for value in [candidate.get('image_id'), *images.values()])):
        raise ValueError('incomplete or invalid bootstrap image set; refusing upstream NF fallback')
    return dict(images)


def candidate_selection(candidate, overrides):
    # A rollback removes the custom override, revealing this baseline, not the
    # old upstream NF. Keep the baseline separate from development history.
    return {**candidate_nf_images(candidate), **overrides}


def render(config, upstream, destination, image, image_overrides=None, database=None):
    upstream, destination = Path(upstream), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    lab = config['lab']
    nets, nodes = lab['networks'], lab['nodes']
    env = Environment(loader=FileSystemLoader(str(ROOT)), undefined=StrictUndefined)
    context = {'lab_config': lab, 'vinbero_device_mode': 'generic'}
    transform = module('compact_free5gc', ROOT / 'ansible/filter_plugins/lab_free5gc.py')
    commit = locks()['sources']['free5gc_compose']['commit']
    for name in ('amf', 'smf', 'upf', 'nrf', 'ausf', 'nssf'):
        original = output(['git', '-C', upstream, 'show', f'{commit}:config/{name}cfg.yaml'])
        write(destination / f'{name}cfg.yaml', transform.render_free5gc(original, name, lab))
    write(destination / 'subscriber.json', env.get_template('ansible/roles/free5gc/templates/subscriber.json.j2').render(**context))
    write(destination / 'mup.yml', env.get_template('ansible/templates/mup-config.yml.j2').render(**context))
    write(destination / 'gnb.yml', env.get_template('ansible/roles/ueransim/templates/gnb.yaml.j2').render(**context))
    write(destination / 'ue.yml', env.get_template('ansible/roles/ueransim/templates/ue.yaml.j2').render(**context))
    write(destination / 'index.html', 'free5GC SRv6 MUP lab data network\n', 0o644)

    original = yaml.safe_load((upstream / 'docker-compose.yaml').read_text())
    # NRF regenerates its certificate. Keep runtime mutations outside the
    # pinned source checkout, and expose this directory read-only to other NFs.
    certificates = destination / 'cert'
    if (upstream / 'cert').is_dir() and not certificates.exists():
        shutil.copytree(upstream / 'cert', certificates)
    services = {name: copy.deepcopy(original['services'][name]) for name in CORE_SERVICES}
    for name, service in services.items():
        service.pop('container_name', None)
        service.pop('ports', None)
        service.pop('build', None)
        service['restart'] = 'on-failure'
        service['image'] = locks()['containers']['images']['mongo' if name == 'db' else name.replace('-', '_')]
        volumes = []
        for item in service.get('volumes', []):
            source, target, *options = item.split(':')
            if source.startswith('./'):
                source = upstream / source[2:]
                if source.name in {f'{nf}cfg.yaml' for nf in ('amf', 'smf', 'upf', 'nrf', 'ausf', 'nssf')}:
                    source = destination / source.name
                mode = 'ro'
                if source == upstream / 'cert':
                    source = certificates
                    if name == 'free5gc-nrf':
                        mode = 'rw'
                volumes.append(f'{source}:{target}:{mode}')
            else:
                volumes.append(item)
        service['volumes'] = volumes
    # No published port: Docker internal networks intentionally omit DNAT.
    # Guest provisioning reaches WebUI using its inspected private address.

    def connection(name, interface, address=None):
        data = {'interface_name': interface,
                'driver_opts': {'com.docker.network.endpoint.sysctls':
                    'net.ipv4.conf.IFNAME.accept_redirects=0,net.ipv4.conf.IFNAME.send_redirects=0'}}
        if address:
            data['ipv6_address' if ':' in address else 'ipv4_address'] = address
        return {name: data}

    services['free5gc-amf']['networks'].update(connection('n2', 'n2', nets['n2']['amf_ipv4']))
    services['free5gc-upf']['networks'].update(connection('n3b', 'n3b', nets['n3_core']['upf_ipv4']))
    services['free5gc-upf']['networks'].update(connection('n6', 'n6', nets['n6']['upf_ipv4']))
    services['free5gc-upf']['command'] = ['bash', '-ec',
        f"ip route replace {nets['n3_access']['gnb_ipv4']}/32 via {nets['n3_core']['tpe_ipv4']} dev n3b; "
        "iptables -I FORWARD 1 -j ACCEPT; exec ./upf -c ./config/upfcfg.yaml"]

    for name in ROLE_SERVICES:
        services[name] = {'image': image, 'restart': 'on-failure', 'init': True,
                          'volumes': [f'{destination}:/run/lab:ro'],
                          'networks': connection('mgmt', 'mgmt', nodes['ran' if name == 'ue' else name]['management_ipv4'])
                          if name not in ('observer',) else {},
                          'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true']}
    services['mupc'].update(command=['mup-controller', '--config', '/run/lab/mup.yml'],
                            cap_add=['NET_BIND_SERVICE'],
                            healthcheck={'test': ['CMD', 'curl', '-fsS', f"http://{nodes['mupc']['management_ipv4']}:9443/healthz"], 'interval': '2s', 'timeout': '2s', 'retries': 30})
    services['observer'].pop('networks')
    # Docker drops effective capabilities when starting a non-root UID. Keep
    # UID 0 with only NET_RAW, not privileged mode or guest Docker access.
    services['observer'].update(network_mode='host', user='0:0', cap_add=['NET_RAW'],
                               command=['pfcp-observer', '--config', '/run/lab/mup.yml'])
    # The observer only needs the public policy, not subscriber authentication data.
    write(destination / 'mup.yml', (destination / 'mup.yml').read_text(), 0o644)
    services['observer']['volumes'] = [f'{destination / "mup.yml"}:/run/lab/mup.yml:ro']
    services['ran']['networks'].update(connection('n2', 'n2', nets['n2']['gnb_ipv4']))
    services['ran']['networks'].update(connection('n3a', 'n3', nets['n3_access']['gnb_ipv4']))
    services['ran'].update(cap_add=['NET_ADMIN', 'NET_RAW'], command=['bash', '/run/lab/ran.sh'])
    services['ran']['sysctls'] = {'net.ipv4.conf.all.accept_redirects': '0',
                                'net.ipv4.conf.default.accept_redirects': '0'}
    write(destination / 'ran.sh', '#!/bin/bash\nset -euo pipefail\n'
          'ethtool -K n3 tx off tso off gso off gro off\n'
          f"ip route replace {nets['n3_core']['upf_ipv4']}/32 via {nets['n3_access']['tpe_ipv4']} dev n3\n"
          'exec nr-gnb -c /run/lab/gnb.yml\n')
    services['ue'].pop('networks')
    services['ue'].update(network_mode='service:ran', cap_add=['NET_ADMIN', 'NET_RAW'],
                          devices=['/dev/net/tun:/dev/net/tun'], command=['nr-ue', '-c', '/run/lab/ue.yml'])
    services['dn']['networks'].update(connection('n6', 'n6', nets['n6']['dn_ipv4']))
    services['dn'].update(cap_add=['NET_ADMIN', 'NET_RAW', 'NET_BIND_SERVICE'], command=['bash', '/run/lab/dn.sh'])
    services['dn']['sysctls'] = {'net.ipv4.conf.all.accept_redirects': '0',
                               'net.ipv4.conf.default.accept_redirects': '0'}
    write(destination / 'dn.sh', '#!/bin/bash\nset -euo pipefail\n'
          # veth generic XDP cannot carry CHECKSUM_PARTIAL through SRv6/GTP
          # rewrites. Complete checksums before packets enter the PE pipeline.
          'ethtool -K n6 tx off tso off gso off gro off\n'
          f"ip route replace {nets['ue']['pool']} via {nets['n6']['npe_ipv4']} dev n6\n"
          f"exec python3 -m http.server 80 --bind {nets['n6']['dn_ipv4']} --directory /run/www\n")
    for role in ('tpe', 'npe'):
        pe = services[role]
        pe.update(cap_add=['NET_ADMIN', 'NET_RAW', 'BPF', 'SYS_ADMIN', 'SYS_RESOURCE', 'NET_BIND_SERVICE'],
                  ulimits={'memlock': {'soft': -1, 'hard': -1}},
                  sysctls={'net.ipv4.ip_forward': '1', 'net.ipv6.conf.all.forwarding': '1',
                           'net.ipv6.conf.all.seg6_enabled': '1', 'net.ipv4.conf.all.rp_filter': '0',
                           'net.ipv4.conf.default.rp_filter': '0'},
                  command=['bash', f'/run/lab/{role}.sh'],
                  healthcheck={'test': ['CMD', 'vinbero', 'stats', 'show'], 'interval': '2s', 'timeout': '2s', 'retries': 30})
        pe['networks'].update(connection('sr', 'sr', nets['sr_underlay'][role + '_ipv6']))
        for scope in ('all', 'default'):
            pe['sysctls'][f'net.ipv4.conf.{scope}.send_redirects'] = '0'
        other = 'npe' if role == 'tpe' else 'tpe'
        commands = ['#!/bin/bash', 'set -euo pipefail',
                    f"ip -6 route replace {nets['sr_underlay'][other + '_locator']} via {nets['sr_underlay'][other + '_ipv6']} dev sr"]
        if role == 'tpe':
            pe['networks'].update(connection('n3a', 'n3', nets['n3_access']['tpe_ipv4']))
            pe['networks'].update(connection('n3b', 'n3b', nets['n3_core']['tpe_ipv4']))
            mapping = {'enp2s0': 'n3', 'enp3s0': 'sr'}
        else:
            pe['networks'].update(connection('n6', 'n6', nets['n6']['npe_ipv4']))
            mapping = {'enp2s0': 'sr', 'enp3s0': 'n6'}
            commands += [f"ip addr replace {lab['mup']['npe_dsd_address']}/32 dev lo",
                         'ip link show mup-dn >/dev/null 2>&1 || ip link add mup-dn type vrf table 100',
                         'ip link set mup-dn up', 'ip link set n6 master mup-dn',
                         f"ip route replace table 100 {nets['n6']['subnet']} dev n6 scope link",
                         f"ip route replace table 100 {nets['ue']['pool']} via {nets['n6']['upf_ipv4']} dev n6 onlink",
                         f"(while true; do ping -I mup-dn -c 1 -W 2 {nets['n6']['dn_ipv4']} >/dev/null 2>&1 || true; sleep 15; done) &"]
        commands += [f'for iface in {" ".join(mapping.values())}; do ethtool -K "$iface" tx off gro off gso off tso off txvlan off rxvlan off; done',
                     f'exec vinberod --bgp-enabled -c /run/lab/{role}.yml']
        write(destination / f'{role}.sh', '\n'.join(commands) + '\n')
        for template, target in [('vinbero.yml.j2', f'{role}.yml'), ('bootstrap.sh.j2', f'{role}-bootstrap.sh')]:
            content = env.get_template('ansible/roles/vinbero_pe/templates/' + template).render(
                **context, pe_role=role, ansible_host=nodes[role]['management_ipv4'])
            for before, after in mapping.items():
                content = content.replace(before, after)
            content = content.replace('ip vrf exec mup-dn ping', 'ping -I mup-dn')
            write(destination / target, content)

    # Never expose subscriber keys through the DN's HTTP document root, or
    # provide every service with all other nodes' configuration files.
    mounts = {
        'mupc': ['mup.yml'], 'ran': ['ran.sh', 'gnb.yml'], 'ue': ['ue.yml'],
        'tpe': ['tpe.sh', 'tpe.yml', 'tpe-bootstrap.sh'],
        'npe': ['npe.sh', 'npe.yml', 'npe-bootstrap.sh'], 'dn': ['dn.sh'],
    }
    for role, names in mounts.items():
        services[role]['volumes'] = [f'{destination / name}:/run/lab/{name}:ro' for name in names]
    services['dn']['volumes'].append(f'{destination / "index.html"}:/run/www/index.html:ro')
    services['dashboard'] = {
        'image': image, 'user': '65534:65534', 'init': True, 'restart': 'on-failure',
        'network_mode': 'none', 'read_only': True, 'cap_drop': ['ALL'],
        'security_opt': ['no-new-privileges:true'],
        'volumes': [f'{destination.parent / "dashboard"}:/run/state:ro',
                    f'{destination.parent / "dashboard-socket"}:/run/socket:rw'],
        'command': ['mup-dashboard', '--state-file', '/run/state/state.json', '--socket', '/run/socket/http.sock'],
        'healthcheck': {'test': ['CMD', 'curl', '-fsS', '--unix-socket', '/run/socket/http.sock', 'http://localhost/healthz'],
                        'interval': '5s', 'timeout': '2s', 'retries': 2},
    }
    for name, replacement in (image_overrides or {}).items():
        if name not in services or name == 'db':
            raise ValueError('unsupported development service override')
        services[name]['image'] = replacement
    for service in services.values():
        fingerprint = hashlib.sha256()
        for volume in service.get('volumes', []):
            source = Path(volume.split(':', 1)[0])
            if source.is_file():
                fingerprint.update(source.name.encode() + source.read_bytes())
        service.setdefault('labels', {})['lab.srv6-mup.config-sha256'] = fingerprint.hexdigest()

    def bridge(subnet, mtu=1500, name=None):
        data = {'driver': 'bridge', 'internal': True,
                'driver_opts': {'com.docker.network.driver.mtu': str(mtu),
                                'com.docker.network.bridge.enable_ip_masquerade': 'false'},
                'ipam': {'config': [{'subnet': subnet}]}}
        if ':' in subnet:
            data.update(enable_ipv4=False, enable_ipv6=True)
        if name:
            data['driver_opts']['com.docker.network.bridge.name'] = name
        return data
    mgmt = nets['management']
    networks = {
        'mgmt': bridge(f"{mgmt['gateway_ipv4']}/{mgmt['prefix_length']}"),
        'privnet': bridge('10.100.200.0/24', name='br-free5gc'),
        'n2': bridge(nets['n2']['subnet']), 'n3a': bridge(nets['n3_access']['subnet']),
        'n3b': bridge(nets['n3_core']['subnet']), 'n6': bridge(nets['n6']['subnet']),
        'sr': bridge(nets['sr_underlay']['subnet'], nets['sr_underlay']['mtu']),
    }
    # IPAM expects a network address, not the host/gateway address.
    import ipaddress
    networks['mgmt']['ipam']['config'][0]['subnet'] = str(ipaddress.ip_network(networks['mgmt']['ipam']['config'][0]['subnet'], strict=False))
    model = {'name': 'srv6-mup-compact', 'services': services, 'networks': networks,
             'volumes': original.get('volumes', {})}
    if database is not None:
        from compact_database import validate
        selected = validate(database)
        if services['db']['volumes'] != ['dbdata:/data/db']:
            raise ValueError('unexpected upstream database mounts')
        services['db']['image'] = selected['image']
        # Avoid anonymous /data/configdb volumes on the standalone lab server.
        services['db']['tmpfs'] = ['/data/configdb']
        model['volumes']['dbdata'] = {'external': True, 'name': selected['volume']}
    write(destination / 'compose.yml', yaml.safe_dump(model, sort_keys=False))
    return model
