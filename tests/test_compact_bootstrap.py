"""Initial image selection must use the same remediated NF build boundary."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_compose as compose
import compact_develop as develop
import compact_runtime as runtime

A, B, C = ('sha256:' + letter * 64 for letter in 'abc')


def candidate():
    return {'image': 'mutable:tag', 'image_id': A, 'image_set_schema': 1,
            'dashboard_snapshot_protocol': 1,
            'nf_images': {name: B for name in compose.CORE_SERVICES if name != 'db'}}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'runtime'
        self.state.mkdir()
        self.staging = self.state / 'bootstrap/test'
        self.staging.mkdir(parents=True)
        database = patch.object(runtime, 'database_selection', return_value={'test': 'database'})
        database.start(); self.addCleanup(database.stop)
        for module, name, value in ((runtime, 'STATE', self.state),
                                    (runtime, 'GENERATED', self.state / 'config'),
                                    (develop, 'STATE', self.state),
                                    (develop, 'DEV', self.state / 'development')):
            item = patch.object(module, name, value)
            item.start(); self.addCleanup(item.stop)

    def test_baseline_is_complete_and_custom_overrides_win(self):
        data = candidate()
        selected = compose.candidate_selection(data, {'free5gc-smf': C, 'dashboard': C})
        self.assertEqual(selected['free5gc-smf'], C)
        self.assertEqual(selected['free5gc-upf'], B)
        self.assertNotIn('db', selected)
        self.assertEqual(len(data['nf_images']), 11)
        self.assertEqual(compose.candidate_selection(data, {})['free5gc-smf'], B)

    def test_legacy_absence_is_distinct_from_invalid_new_baseline(self):
        self.assertEqual(compose.candidate_nf_images({'image_id': A}), {})
        for change in ({'nf_images': {}}, {'nf_images': None}, {'nf_images': []},
                       {'image_set_schema': 2}, {'image_id': 'local:latest'},
                       {'nf_images': {'free5gc-smf': B}},
                       {'nf_images': {**candidate()['nf_images'], 'db': B}},
                       {'nf_images': {**candidate()['nf_images'], 'free5gc-smf': None}}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'upstream NF fallback'):
                compose.candidate_nf_images({**candidate(), **change})
        with self.assertRaises(ValueError):
            compose.candidate_nf_images({'image_id': A, 'image_set_schema': 1})

    def test_all_nfs_use_clean_build_only_and_separate_locked_checkout(self):
        names = []
        def build(name, path, **kwargs):
            names.append(name)
            self.assertEqual(kwargs, {'clean_runtime': True, 'local_builder': True})
            return {'image_id': B}
        with patch.object(runtime, 'checkout', return_value=self.root / 'locked') as checkout, \
             patch.object(runtime, 'output', return_value=''), \
             patch.object(develop, 'snapshot') as snapshot, \
             patch.object(develop, 'build_image', side_effect=build), \
             patch.object(develop, 'source') as editable, patch.object(develop, 'activate') as activate:
            result = runtime.build_nf_images(self.staging)
        self.assertEqual(set(names), set(compose.CORE_SERVICES) - {'db'})
        self.assertEqual(set(result['nf_build_records']), set(names))
        self.assertEqual(checkout.call_count, 11)
        self.assertTrue(all(call.kwargs['root'] == self.root / 'locked' for call in snapshot.call_args_list))
        editable.assert_not_called(); activate.assert_not_called()

    def test_dirty_bootstrap_sources_are_rejected_without_reset(self):
        with patch.object(runtime, 'checkout', return_value=self.root), \
             patch.object(runtime, 'output', return_value=' M go.mod'), \
             patch.object(develop, 'snapshot') as snapshot:
            with self.assertRaisesRegex(ValueError, 'has edits'):
                runtime.build_nf_images(self.staging)
        snapshot.assert_not_called()

    def test_build_failures_preserve_candidate_config_and_custom_history(self):
        paths = [self.state / 'candidate.json', self.state / 'config/compose.yml',
                 develop.DEV / 'overrides.json', develop.DEV / 'history.json']
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        before = {path: path.read_bytes() for path in paths}
        for failure in ('nf', 'validation'):
            with patch.object(runtime, 'build_nf_images',
                              side_effect=ValueError('last NF failed') if failure == 'nf' else None,
                              return_value={'nf_images': candidate()['nf_images']}), \
                 patch.object(runtime, 'checkout', return_value=self.root), \
                 patch.object(runtime, 'render') as render, \
                 patch.object(runtime, 'run', side_effect=subprocess.CalledProcessError(1, 'config')):
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    runtime.complete_candidate({}, self.staging, {'image_id': A})
            self.assertEqual(before, {path: path.read_bytes() for path in paths})
            if failure == 'validation':
                self.assertEqual(render.call_args.args[2], self.staging / 'config')

    def test_candidate_commits_only_after_complete_staged_validation(self):
        with patch.object(runtime, 'build_nf_images', return_value={'nf_images': candidate()['nf_images']}), \
             patch.object(runtime, 'checkout', return_value=self.root), \
             patch.object(runtime, 'render') as render, patch.object(runtime, 'run') as run:
            runtime.complete_candidate({}, self.staging, {'image_id': A})
        selected = json.loads((self.state / 'candidate.json').read_text())
        self.assertEqual(compose.candidate_nf_images(selected), candidate()['nf_images'])
        self.assertEqual(render.call_args.args[3], A)
        self.assertIn(self.staging / 'config/compose.yml', run.call_args.args[0])
        self.assertFalse((self.state / 'config').exists())
        self.assertFalse((develop.DEV / 'overrides.json').exists())

    def test_pending_activation_blocks_baseline_build_before_provisioning(self):
        develop.atomic(develop.DEV / 'pending.json', {})
        with patch.object(runtime, 'prepare') as prepare, self.assertRaisesRegex(ValueError, 'unfinished activation'):
            runtime.build({})
        prepare.assert_not_called()

    def test_dashboard_only_deploy_keeps_baseline_and_custom_images(self):
        develop.atomic(self.state / 'candidate.json', candidate())
        develop.atomic(develop.DEV / 'overrides.json', {'free5gc-smf': C, 'dashboard': C})
        with patch.object(runtime, 'checkout', return_value=self.root), \
             patch.object(compose, 'render') as render, patch.object(runtime, 'compose'), \
             patch.object(runtime, 'dashboard_install'):
            develop.deploy({}, {'dashboard'})
        self.assertEqual(render.call_args.args[3], A)
        self.assertEqual(render.call_args.args[4]['free5gc-smf'], C)
        self.assertEqual(render.call_args.args[4]['free5gc-amf'], B)

    def test_missing_baseline_image_fails_before_compose_mutation(self):
        develop.atomic(self.state / 'candidate.json', candidate())
        with patch.object(runtime, 'output', side_effect=subprocess.CalledProcessError(1, 'inspect')), \
             patch.object(runtime, 'render') as render, patch.object(runtime, 'compose') as command:
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.up({})
        render.assert_not_called(); command.assert_not_called()

    def test_clean_artifact_build_needs_no_previous_deployment(self):
        path = self.staging / ('a' * 32)
        path.mkdir()
        def run(command):
            if command[:2] == ['docker', 'run']:
                (path / 'output/smf').write_text('built')
                (path / 'provenance/go.mod').write_text('effective locks')
        with patch.object(develop, 'unpack', return_value={'source_sha256': 'test'}), \
             patch.object(develop, 'builder', return_value={'image_id': C}), \
             patch.object(develop.os, 'chown'), patch.object(develop, 'run', side_effect=run), \
             patch.object(develop, 'output', return_value=B), \
             patch.object(develop, 'resolve_image') as resolve, patch.object(develop, 'activate') as activate:
            record = develop.build_image('free5gc-smf', path, clean_runtime=True)
        resolve.assert_not_called(); activate.assert_not_called()
        self.assertIsNone(record['previous_image_id'])
        self.assertTrue(record['clean_runtime'])
        self.assertIn('go.mod', record['effective_module_locks'])
        self.assertEqual(record['dependency_policy_sha256'], develop.digest(ROOT / 'config/compact-dependencies.json'))
