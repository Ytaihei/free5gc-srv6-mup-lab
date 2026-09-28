"""Offline fault/gate checks for guest-scoped recovery scenarios."""
import copy
import fcntl
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_config
import compact_recovery as recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        profile = self.root / 'profile.yml'
        profile.write_text('{}')
        self.config = compact_config.load(profile)
        active = patch.object(recovery.runtime, 'STATE', self.root)
        active.start(); self.addCleanup(active.stop)

    def network(self):
        nets = self.config['lab']['networks']
        return [[{'gateway': nets['sr_underlay']['npe_ipv6'], 'dev': 'sr'}],
                [{'gateway': nets['sr_underlay']['tpe_ipv6'], 'dev': 'sr'}],
                [{'master': 'mup-dn'}],
                [{'linkinfo': {'info_kind': 'vrf', 'info_data': {'table': 100}}}],
                [{'dev': 'n6', 'scope': 'link'}],
                [{'dev': 'n6', 'gateway': nets['n6']['upf_ipv4']}]]

    def test_real_routes_vrf_and_fallback_are_required(self):
        with patch.object(recovery.runtime, 'execute', side_effect=map(json.dumps, self.network())):
            self.assertIn('ue_vrf_route', recovery.network_state(self.config))
        for index in range(6):
            data = self.network(); data[index] = []
            with self.subTest(index=index), \
                 patch.object(recovery.runtime, 'execute', side_effect=map(json.dumps, data)), \
                 self.assertRaises(ValueError):
                recovery.network_state(self.config)

    def test_wrong_vrf_table_and_next_hop_fail(self):
        data = self.network()
        data[3][0]['linkinfo']['info_data']['table'] = 200
        with patch.object(recovery.runtime, 'execute', side_effect=map(json.dumps, data)), \
             self.assertRaisesRegex(ValueError, 'table 100'):
            recovery.network_state(self.config)
        data = self.network(); data[0][0]['gateway'] = '2001:db8::bad'
        with patch.object(recovery.runtime, 'execute', side_effect=map(json.dumps, data)), \
             self.assertRaisesRegex(ValueError, 'locator'):
            recovery.network_state(self.config)

    def test_restart_and_namespace_recreation_cannot_be_noops(self):
        before = {pe: {'id': pe, 'image': 'image', 'started': 'old', 'namespace': pe} for pe in ('tpe', 'npe')}
        after = copy.deepcopy(before)
        with self.assertRaises(ValueError):
            recovery.changed(before, after, False)
        for pe in after:
            after[pe]['started'] = 'new'
        recovery.changed(before, after, False)
        with self.assertRaises(ValueError):
            recovery.changed(before, after, True)
        for pe in after:
            after[pe].update(id=pe + '-new', namespace=pe + '-new')
        recovery.changed(before, after, True)
        after['npe']['image'] = 'different-image'
        with self.assertRaises(ValueError):
            recovery.changed(before, after, True)

    def test_foreign_pe_container_is_refused(self):
        item = {'Config': {'Labels': {'com.docker.compose.project': 'foreign'}}, 'State': {'Running': True}}
        with patch.object(recovery.runtime, 'compose', return_value='id'), \
             patch.object(recovery, 'output', return_value=json.dumps([item])), \
             self.assertRaisesRegex(ValueError, 'ownership'):
            recovery.pe_state()

    def test_packet_evidence_records_actual_custom_images_without_container_secrets(self):
        item = {'Config': {'Labels': {'com.docker.compose.project': 'srv6-mup-compact',
                                     'com.docker.compose.service': 'free5gc-smf'},
                           'Env': ['SECRET=not-for-evidence']},
                'Image': 'custom-immutable-image', 'Id': 'container'}
        with patch.object(recovery.runtime, 'compose', return_value='container'), \
             patch.object(recovery.runtime, 'output', return_value=json.dumps([item])):
            result = recovery.runtime.runtime_images()
        self.assertEqual(result, {'free5gc-smf': {'image_id': 'custom-immutable-image', 'container_id': 'container'}})

    def test_neighbor_failure_and_static_entries_are_not_healthy(self):
        for state in ('INCOMPLETE', 'FAILED', 'PERMANENT', 'NOARP'):
            self.assertFalse(recovery.usable_neighbor([{'lladdr': '02:00:00:00:00:01', 'state': [state]}]))
        self.assertTrue(recovery.usable_neighbor([{'lladdr': '02:00:00:00:00:01', 'state': ['STALE']}]))

    def test_neighbor_recovery_excludes_collector_and_manual_probes(self):
        healthy = [{'lladdr': '02:00:00:00:00:01', 'state': ['REACHABLE']}]
        def wait(description, check, timeout):
            with (self.root / 'probe.lock').open('a') as probe:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            return check()
        with patch.object(recovery, 'network_state', return_value={}), \
             patch.object(recovery, 'neighbor_state', side_effect=[healthy, [], healthy]), \
             patch.object(recovery.runtime, 'wait_for', side_effect=wait), \
             patch.object(recovery.runtime, 'execute') as execute, \
             patch.object(recovery.runtime, 'traffic') as traffic, \
             patch.object(recovery.runtime, 'one_call'):
            result = recovery.neighbor_recovery(self.config)
        execute.assert_called_once_with('npe', 'ip', 'neigh', 'del',
                                       self.config['lab']['networks']['n6']['dn_ipv4'], 'dev', 'n6')
        self.assertFalse(result['manual_refresh_used'])
        traffic.assert_called_once()
        with (self.root / 'probe.lock').open('a') as probe:
            fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_failed_neighbor_recovery_releases_probe_lock_and_does_not_pass_traffic(self):
        with patch.object(recovery, 'network_state', return_value={}), \
             patch.object(recovery.runtime, 'wait_for', side_effect=ValueError('timeout')), \
             patch.object(recovery.runtime, 'traffic') as traffic, self.assertRaises(ValueError):
            recovery.neighbor_recovery(self.config)
        traffic.assert_not_called()
        with (self.root / 'probe.lock').open('a') as probe:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_recreate_is_scoped_and_old_session_is_proved_before_reconnect(self):
        events = []
        with patch.object(recovery.runtime, 'wait_session', return_value={'session': {'key': 'same'}}), \
             patch.object(recovery, 'pe_state', return_value={}), \
             patch.object(recovery, 'changed'), patch.object(recovery, 'ready'), \
             patch.object(recovery, 'network_state', return_value={}), \
             patch.object(recovery.runtime, 'compose') as compose, \
             patch.object(recovery.runtime, 'traffic', side_effect=lambda _: events.append('traffic')), \
             patch.object(recovery.runtime, 'baseline', side_effect=lambda _: events.append('baseline')), \
             patch.object(recovery.runtime, 'one_call', side_effect=lambda _: events.append('one-call')):
            recovery.pe_recovery(self.config, True)
        compose.assert_called_once_with('up', '-d', '--no-deps', '--force-recreate', 'tpe', 'npe')
        self.assertEqual(events, ['traffic', 'baseline', 'one-call'])

    def test_changed_pfcp_session_is_not_hidden_by_reconnect(self):
        with patch.object(recovery.runtime, 'wait_session', side_effect=[{'session': {'key': 'old'}}, {'session': {'key': 'new'}}]), \
             patch.object(recovery, 'pe_state', return_value={}), \
             patch.object(recovery, 'changed'), patch.object(recovery, 'ready'), \
             patch.object(recovery, 'network_state', return_value={}), \
             patch.object(recovery.runtime, 'compose') as compose, \
             patch.object(recovery.runtime, 'one_call') as call, \
             self.assertRaisesRegex(ValueError, 'PFCP session'):
            recovery.pe_recovery(self.config, False)
        call.assert_not_called()
        self.assertEqual(compose.call_count, 2)  # restart plus best-effort restoration

    def test_failed_gate_has_failed_evidence_even_if_restoration_succeeds(self):
        with patch.object(recovery, 'network_state', return_value={}), \
             patch.object(recovery, 'pe_recovery', side_effect=ValueError('packet gate failed')), \
             self.assertRaises(ValueError):
            recovery.run(self.config, 'restart')
        result = json.loads(next(self.root.glob('evidence/*/result.json')).read_text())
        self.assertFalse(result['passed'])
        self.assertEqual(result['error'], 'packet gate failed')

    def test_unknown_scenario_cannot_mutate_lab(self):
        with patch.object(recovery.runtime, 'compose') as compose, self.assertRaises(ValueError):
            recovery.run(self.config, 'unknown')
        compose.assert_not_called()
