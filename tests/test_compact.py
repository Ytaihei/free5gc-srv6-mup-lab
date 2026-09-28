"""Offline checks for the single-VM boundary and declarative network model."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_compose
import compact_config
import compact_evidence
import compact_runtime
import compact_collector
import compact_dashboard
import compact_diagnostics
import compact_vm
from compact_vm import VM


class CompactConfigTests(unittest.TestCase):
    def config(self, content=None):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'profile.yml'
            path.write_text(yaml.safe_dump(content or {}))
            return compact_config.load(path)

    def test_default_is_one_eight_gib_vm(self):
        config = self.config()
        self.assertEqual(config['vm']['memory_mib'], 8192)
        self.assertEqual(config['vm']['vcpus'], 4)
        self.assertEqual(config['lab']['subscriber']['dnn'], 'internet')

    def test_new_base_remains_group_readable_with_private_umask(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'image.img'
            content = b'verified public distribution image'
            spec = {'url': 'https://example.invalid/image', 'sha256': hashlib.sha256(content).hexdigest()}
            previous_umask = os.umask(0o077)
            try:
                with patch.object(compact_vm, 'run', side_effect=lambda _: base.with_suffix('.partial').write_bytes(content)), \
                     patch.object(compact_vm.shutil, 'chown') as chown:
                    compact_vm.prepare_base_image(base, spec)
                chown.assert_called_once_with(base.with_suffix('.partial'), group='kvm')
                self.assertEqual(base.stat().st_mode & 0o777, 0o640)
                self.assertEqual(base.read_bytes(), content)
            finally:
                os.umask(previous_umask)

    def test_cached_base_is_verified_without_replacing_permissions_or_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'image.img'
            base.write_bytes(b'existing'); base.chmod(0o644)
            spec = {'sha256': hashlib.sha256(b'existing').hexdigest()}
            with patch.object(compact_vm, 'run') as run, patch.object(compact_vm.shutil, 'chown') as chown:
                compact_vm.prepare_base_image(base, spec)
            run.assert_not_called(); chown.assert_not_called()
            self.assertEqual(base.stat().st_mode & 0o777, 0o644)
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                compact_vm.prepare_base_image(base, {'sha256': 'wrong'})

    def test_cloud_image_symlinks_are_refused_before_download(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'image.img'
            base.symlink_to(Path(temp) / 'missing')
            with patch.object(compact_vm, 'run') as run, self.assertRaisesRegex(ValueError, 'symlink'):
                compact_vm.prepare_base_image(base, {})
            run.assert_not_called()

    def test_partial_logical_override_preserves_other_defaults(self):
        config = self.config({'lab': {'subscriber': {'dnn': 'custom'}}})
        self.assertEqual(config['lab']['subscriber']['dnn'], 'custom')
        self.assertEqual(config['lab']['mup']['observer_lease'], '15s')

    def test_explicit_missing_config_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            compact_config.load('/missing-compact-config.yml')

    def test_reject_unsafe_or_unsupported_settings(self):
        for content in ({'unexpected': 1}, {'vm': {'name': 'bad;command'}},
                        {'vm': {'image_directory': '/home'}}, {'vm': {'memory_mib': True}},
                        {'lab': {'nodes': {}}}, {'dashboard_port': 80},
                        {'vm': {'bridge': 'bridge-name-too-long'}},
                        {'vm': {'subnet': '10.100.200.0/24', 'gateway': '10.100.200.1', 'address': '10.100.200.20'}},
                        {'vm': {'subnet': '192.168.123.0/24', 'gateway': '192.168.123.1',
                                'address': '192.168.123.20'}}):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.config(content)

    def test_resources_are_not_adopted_without_ownership(self):
        vm = VM(self.config())
        with tempfile.TemporaryDirectory() as temp:
            vm.state = Path(temp)
            with self.assertRaisesRegex(ValueError, 'ownership'):
                vm.owned()

    def test_source_archive_excludes_local_state_and_developer_worktrees(self):
        module = compact_config.module('compact_export_test', ROOT / 'scripts/export-source.py')
        self.assertIn('.lab', module.BLOCKED_DIRS)
        self.assertIn('worktrees', module.BLOCKED_DIRS)
        files = json.loads((ROOT / 'config/public-source.json').read_text())['files']
        self.assertFalse(any(name.startswith(('.lab/', 'worktrees/')) for name in files))


class CompactTopologyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        profile = self.root / 'profile.yml'
        profile.write_text('{}')
        self.config = compact_config.load(profile)
        upstream = self.root / 'upstream'
        upstream.mkdir()
        (upstream / 'cert').mkdir()
        (upstream / 'cert/nrf.pem').write_text('test certificate')
        services = {}
        for name in compact_compose.CORE_SERVICES:
            services[name] = {'image': 'not-used:latest', 'container_name': name,
                              'networks': {'privnet': {'aliases': [name]}},
                              'ports': ['5000:5000'], 'volumes': ['./config/amfcfg.yaml:/config.yml']}
        for name in ('free5gc-nrf', 'free5gc-amf'):
            services[name]['volumes'].append('./cert:/free5gc/cert')
        (upstream / 'docker-compose.yaml').write_text(yaml.safe_dump({'services': services, 'volumes': {'dbdata': {}}}))
        class FakeTransform:
            @staticmethod
            def render_free5gc(source, name, lab):
                return 'configuration: {}\n'
        with patch.object(compact_compose, 'output', return_value='configuration: {}'), \
             patch.object(compact_compose, 'module', return_value=FakeTransform):
            self.model = compact_compose.render(self.config, upstream, self.root / 'generated', 'local:candidate')

    def test_all_seven_networks_are_internal(self):
        networks = self.model['networks']
        self.assertEqual(set(networks), {'mgmt', 'privnet', 'n2', 'n3a', 'n3b', 'sr', 'n6'})
        for net in networks.values():
            self.assertTrue(net['internal'])
            self.assertEqual(net['driver'], 'bridge')
            self.assertEqual(net['driver_opts']['com.docker.network.bridge.enable_ip_masquerade'], 'false')
        self.assertFalse(networks['sr']['enable_ipv4'])
        self.assertTrue(networks['sr']['enable_ipv6'])

    def test_no_unscoped_container_names_or_external_ports(self):
        for name, service in self.model['services'].items():
            self.assertNotIn('container_name', service)
            self.assertFalse(service.get('privileged', False))
            for port in service.get('ports', []):
                self.assertTrue(port.startswith('127.0.0.1:'))

    def test_observer_only_captures_inside_guest(self):
        observer = self.model['services']['observer']
        self.assertEqual(observer['network_mode'], 'host')
        self.assertEqual(observer['cap_add'], ['NET_RAW'])
        self.assertEqual(observer['user'], '0:0')
        self.assertEqual(observer['cap_drop'], ['ALL'])
        self.assertNotIn('networks', observer)
        self.assertEqual(len(observer['volumes']), 1)
        self.assertIn('mup.yml', observer['volumes'][0])

    def test_ue_shares_ran_netns_but_has_separate_lifecycle(self):
        ue = self.model['services']['ue']
        self.assertEqual(ue['network_mode'], 'service:ran')
        self.assertNotIn('networks', ue)
        self.assertIn('/dev/net/tun:/dev/net/tun', ue['devices'])

    def test_only_nrf_may_write_runtime_certificates(self):
        services = self.model['services']
        self.assertIn(f'{self.root}/generated/cert:/free5gc/cert:rw', services['free5gc-nrf']['volumes'])
        self.assertIn(f'{self.root}/generated/cert:/free5gc/cert:ro', services['free5gc-amf']['volumes'])
        self.assertEqual((self.root / 'generated/cert/nrf.pem').read_text(), 'test certificate')
        self.assertNotIn('ports', services['free5gc-webui'])

    def test_pe_interfaces_are_stable_and_generic_mode_is_explicit(self):
        for role in ('tpe', 'npe'):
            config = yaml.safe_load((self.root / 'generated' / f'{role}.yml').read_text())
            self.assertEqual(config['internal']['bpf']['device_mode'], 'generic')
            actual = {net['interface_name'] for net in self.model['services'][role]['networks'].values()}
            self.assertTrue(set(config['internal']['devices']) <= actual)
            self.assertFalse(config['settings']['pin_maps']['enabled'])

    def test_dn_never_serves_authentication_configuration(self):
        dn = self.model['services']['dn']
        self.assertEqual(len(dn['volumes']), 2)
        self.assertTrue(any('/run/www/index.html:ro' in item for item in dn['volumes']))
        self.assertFalse(any('ue.yml' in item or 'subscriber' in item for item in dn['volumes']))
        self.assertIn('--directory /run/www', (self.root / 'generated/dn.sh').read_text())

    def test_upf_does_not_masquerade_the_ue_on_n6(self):
        command = str(self.model['services']['free5gc-upf']['command'])
        self.assertNotIn('MASQUERADE', command)
        self.assertIn('ip route replace', command)

    def test_npe_fallback_and_neighbor_recovery_are_reapplied_at_start(self):
        startup = (self.root / 'generated/npe.sh').read_text()
        self.assertIn('table 100', startup)
        self.assertIn('onlink', startup)
        self.assertIn('sleep 15', startup)
        self.assertIn('exec vinberod', startup)

    def test_generic_xdp_sources_complete_checksums_and_disable_redirects(self):
        for name, interface in (('ran', 'n3'), ('dn', 'n6')):
            self.assertIn(f'ethtool -K {interface} tx off', (self.root / 'generated' / f'{name}.sh').read_text())
            endpoint = self.model['services'][name]['networks']['n3a' if name == 'ran' else 'n6']
            self.assertIn('IFNAME.accept_redirects=0', endpoint['driver_opts']['com.docker.network.endpoint.sysctls'])
        self.assertEqual(self.model['services']['npe']['sysctls']['net.ipv4.conf.all.send_redirects'], '0')

    def test_configuration_hash_is_present_for_recreation(self):
        for service in self.model['services'].values():
            self.assertRegex(service['labels']['lab.srv6-mup.config-sha256'], r'^[a-f0-9]{64}$')

    def test_dashboard_has_no_network_or_privileged_mounts(self):
        web = self.model['services']['dashboard']
        self.assertEqual(web['network_mode'], 'none')
        self.assertEqual(web['user'], '65534:65534')
        self.assertEqual(web['cap_drop'], ['ALL'])
        self.assertTrue(web['read_only'])
        self.assertNotIn('ports', web)
        self.assertNotIn('cap_add', web)
        self.assertEqual(len(web['volumes']), 2)
        self.assertTrue(web['volumes'][0].endswith(':/run/state:ro'))
        self.assertTrue(web['volumes'][1].endswith(':/run/socket:rw'))


class CompactDashboardTests(unittest.TestCase):
    def test_probe_does_not_run_during_packet_gate(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(compact_collector, 'STATE', Path(temp)), \
             patch.object(compact_collector, 'execute') as execute:
            with (Path(temp) / 'probe.lock').open('a') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                result = compact_collector.probe('10.210.6.15')
            self.assertEqual(result['status'], 'idle')
            self.assertFalse(result['success'])
            execute.assert_not_called()

    def test_failed_collection_never_reuses_healthy_state(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / 'config.yml'
            profile.write_text('{}')
            with patch.object(compact_collector, 'query', side_effect=OSError), \
                 patch.object(compact_collector, 'probe', return_value={'status': 'failed', 'success': False}):
                state = compact_collector.collect(compact_config.load(profile))
        self.assertEqual(state['overall'], 'offline')
        self.assertFalse(state['paths']['mup_active'])
        self.assertFalse(state['paths']['fallback_active'])
        self.assertTrue(state['errors'])
        self.assertTrue(all(not node['healthy'] for node in state['nodes']))
        labels = {'tpe': 'MUP PE (N3/Interwork side)', 'npe': 'MUP PE (N6/Direct side)'}
        self.assertEqual({pe['id']: pe['name'] for pe in state['pes']}, labels)
        for node in state['nodes']:
            if node['id'] in labels:
                self.assertEqual(node['name'], node['id'])
                self.assertEqual(node['role'], labels[node['id']])

    def test_dashboard_binding_is_opt_in_and_rejects_public_ip(self):
        vm = VM({'vm': {'name': 'test', 'image_directory': '/var/lib/libvirt/images'}, 'dashboard_port': 8788})
        tunnel = compact_dashboard.Dashboard(vm)
        with patch.object(compact_dashboard, 'output', return_value='100.64.1.2') as output:
            self.assertEqual(tunnel.bindings(False), ['127.0.0.1'])
            output.assert_not_called()
            self.assertEqual(tunnel.bindings(True), ['127.0.0.1', '100.64.1.2'])
        for address in ('0.0.0.0', '8.8.8.8', '127.0.0.1', '::1'):
            with patch.object(compact_dashboard, 'output', return_value=address), self.assertRaises(ValueError):
                tunnel.bindings(True)

    def test_unowned_user_service_cannot_be_stopped(self):
        vm = VM({'vm': {'name': 'test', 'image_directory': '/var/lib/libvirt/images'}})
        tunnel = compact_dashboard.Dashboard(vm)
        with patch.object(vm, 'owned', return_value={'domain_uuid': 'expected'}), \
             patch.object(compact_dashboard, 'output', return_value='LoadState=loaded\nDescription=unrelated'), \
             patch.object(compact_dashboard, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'ownership'):
                tunnel.stop()
            run.assert_not_called()

    def test_diagnostics_never_sends_a_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / 'config.yml'
            profile.write_text('{}')
            with patch.object(compact_collector, 'query', side_effect=OSError), \
                 patch.object(compact_collector, 'probe') as probe:
                state = compact_collector.collect(compact_config.load(profile), active_probe=False)
            probe.assert_not_called()
            self.assertEqual(state['uplane_probe']['status'], 'idle')

    def test_diagnostic_errors_do_not_include_raw_secrets(self):
        results = {}
        def fail():
            raise ValueError('secret text')
        compact_diagnostics.check(results, 'test', fail)
        self.assertEqual(results, {'test': {'ok': False, 'error': 'ValueError'}})


class CompactRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        profile = self.root / 'config.yml'
        profile.write_text('{}')
        self.vm = VM(compact_config.load(profile))
        self.vm.state = self.root / 'state' / self.vm.vm['name']
        self.vm.state.mkdir(parents=True)
        self.vm.disk = self.root / (self.vm.vm['name'] + '.qcow2')
        self.vm.disk.write_text('retained disk')
        self.owner = patch.object(self.vm, 'owned', return_value={'domain_uuid': 'owned-uuid'})
        self.owner.start()
        self.addCleanup(self.owner.stop)

    def output(self, args):
        if 'list' in args:
            return self.vm.vm['name']
        if 'dumpxml' in args:
            return f'<domain><devices><disk device="disk"><source file="{self.vm.disk}"/></disk></devices></domain>'
        return ''

    def test_dry_run_and_wrong_confirmation_do_not_mutate(self):
        with patch.object(compact_vm, 'output', side_effect=self.output), patch.object(compact_vm, 'run') as run:
            self.vm.retire()
            with self.assertRaisesRegex(ValueError, 'confirmation'):
                self.vm.retire('wrong')
            run.assert_not_called()
            self.assertTrue(self.vm.disk.exists())

    def test_extra_disks_or_network_users_block_retirement(self):
        with patch.object(compact_vm, 'output', side_effect=lambda args:
                self.vm.vm['name'] if 'list' in args else '<domain><devices/></domain>'):
            with self.assertRaisesRegex(ValueError, 'disks'):
                self.vm.retirement_plan()
        with patch.object(compact_vm, 'output', side_effect=lambda args:
                'unrelated' if 'list' in args else
                f'<domain><devices><interface><source network="{self.vm.vm["network"]}"/></interface></devices></domain>'):
            with self.assertRaisesRegex(ValueError, 'another domain'):
                self.vm.retirement_plan()

    def test_apply_archives_data_without_deleting_it(self):
        (self.vm.state / 'owner.json').write_text('retained ownership')
        with patch.object(compact_vm, 'output', side_effect=self.output), \
             patch.object(compact_vm, 'run') as run, patch.object(self.vm, 'stop'), \
             patch.object(compact_dashboard.Dashboard, 'stop'):
            plan = self.vm.retirement_plan()
            self.vm.retire(self.vm.vm['name'])
        self.assertEqual(Path(plan['retained_disk']).read_text(), 'retained disk')
        self.assertEqual((Path(plan['retained_state']) / 'owner.json').read_text(), 'retained ownership')
        self.assertIn('<domain>', (Path(plan['retained_state']) / 'retired-domain.xml').read_text())
        self.assertFalse(self.vm.disk.exists())
        self.assertTrue(any('undefine' in call.args[0] for call in run.call_args_list))


class CompactEvidenceTests(unittest.TestCase):
    def test_kernel_gate_rejects_reused_old_module(self):
        with patch.object(compact_runtime, 'output', return_value='6.8.0-138-generic SMP modversions'):
            self.assertFalse(compact_runtime.module_matches('6.8.0-139-generic'))
            self.assertTrue(compact_runtime.module_matches('6.8.0-138-generic'))

    @staticmethod
    def packet(protocol=1, payload=None):
        if payload is None:
            payload = bytes.fromhex('0800000001230001')
        return struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(payload), 0, 0, 64,
                           protocol, 0, bytes([10, 60, 0, 1]), bytes([10, 210, 6, 15])) + payload

    def test_gtp_optional_extension_decodes_teid_and_same_echo(self):
        inner = self.packet()
        gtp = bytes.fromhex('34ff002c000000290000008501100100') + inner
        udp = struct.pack('!HHHH', 2152, 2152, 8 + len(gtp), 0) + gtp
        frame = b'\0' * 12 + b'\x08\x00' + self.packet(17, udp)
        result = compact_evidence.decode(frame)
        self.assertEqual(result['teid'], 41)
        self.assertEqual(result['echo'], ('10.60.0.1', '10.210.6.15', 291, 1))

    def test_srv6_single_segment_reduced_encapsulation(self):
        inner = self.packet()
        ip6 = bytes.fromhex('60000000') + len(inner).to_bytes(2, 'big') + b'\x04\x40' + b'\0' * 32
        result = compact_evidence.decode(b'\0' * 12 + b'\x86\xdd' + ip6 + inner)
        self.assertEqual(result['encapsulation'], 'srv6')
        self.assertEqual(result['echo'], compact_evidence.ipv4(inner)['echo'])

    def test_rejects_truncated_ipv4_and_gtp(self):
        with self.assertRaises(ValueError):
            compact_evidence.ipv4(self.packet()[:-1])
        with self.assertRaises(ValueError):
            compact_evidence.decode(b'\0' * 12 + b'\x86\xdd' + b'\0')

    def test_pcap_parser_rejects_truncation_and_other_link_types(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test.pcap'
            for data in (b'bad', struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 113),
                         struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1) + b'\0'):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    list(compact_evidence.records(path))

    def test_bpf_maps_are_resolved_per_attachment_not_global_name(self):
        maps = [{'id': i, 'name': 'mup_uplink_v4_m', 'type': 'lpm_trie'} for i in (11, 22, 99)]
        programs = [{'id': 1, 'map_ids': [11]}, {'id': 2, 'map_ids': [22]}]
        def mock_output(args):
            if args[2:4] == ['map', 'show']:
                return json.dumps(maps)
            if args[2:4] == ['prog', 'show']:
                return json.dumps(programs)
            return '[]'
        def execute(role, *args):
            return json.dumps([{'xdp': {'mode': 2, 'prog': {'id': 1 if role == 'tpe' else 2}}}])
        with patch.object(compact_evidence, 'output', side_effect=mock_output):
            result = compact_evidence.bpf_state(execute)
        self.assertEqual([m['id'] for m in result['tpe']['maps']], [11])
        self.assertEqual([m['id'] for m in result['npe']['maps']], [22])

    def test_packet_gate_rejects_upf_bypass_and_missing_echoes(self):
        ue, dn = '10.60.0.1', '10.210.6.15'
        session = {'ue_ipv4': ue, 'uplink_teid': 4, 'downlink_teid': 8}
        packets = []
        for seq in range(1, 9):
            for src, dst, teid in ((ue, dn, 4), (dn, ue, 8)):
                packets.append({'src': src, 'dst': dst, 'protocol': 1, 'encapsulation': 'gtpu',
                                'teid': teid, 'echo': (src, dst, 17, seq)})
        packets.append({'src': dn, 'dst': ue, 'protocol': 6, 'encapsulation': 'gtpu',
                        'teid': 8, 'tcp_checksum_valid': True})
        points = {'n3': packets, 'n6': packets, 'sr': packets, 'upf-n3': [], 'upf-n6': []}
        with patch.object(compact_evidence, 'records', side_effect=lambda path: points[path.stem]), \
             patch.object(compact_evidence, 'decode', side_effect=lambda packet: packet):
            self.assertTrue(compact_evidence.verify_packets('/unused', session, dn, True)['path_verified'])
            points['upf-n6'] = packets[:1]
            with self.assertRaisesRegex(ValueError, 'UPF'):
                compact_evidence.verify_packets('/unused', session, dn, True)
            points['upf-n6'] = []
            points['sr'] = packets[:-3]
            with self.assertRaisesRegex(ValueError, 'missing test echo'):
                compact_evidence.verify_packets('/unused', session, dn, True)

    def test_control_capture_cannot_pass_with_empty_files(self):
        with patch.object(compact_evidence, 'records', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                compact_evidence.verify_control('/unused')
