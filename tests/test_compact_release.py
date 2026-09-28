"""Offline release boundaries: no invented publication, fallback, or live upgrade."""
import copy
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
import compact_ci as ci
import compact_develop as develop
import compact_lab as launcher
import compact_release as release
import compact_runtime as runtime

A, B, C = ('sha256:' + x * 64 for x in 'abc')


def image(name, identifier=A):
    return {'reference': 'ghcr.io/example/' + name + '@' + identifier, 'image_id': identifier}


def manifest():
    return {'schema_version': 1, 'release': {
        'version': 'v0.1.0-test', 'platform': 'linux/amd64',
        'compatibility_sha256': 'd' * 64, 'source_sha256': 'e' * 64,
        'runtime': image('runtime'),
        'nf_images': {name: image(name, B) for name in compose.CORE_SERVICES if name != 'db'},
        'builder': {'image': image('builder', C), 'inputs': {'locked': True}},
        'database': release.locks()['compact']['database']['image'],
        'dashboard_snapshot_protocol': 1}}


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'runtime'
        self.state.mkdir()
        self.dev = self.state / 'development'
        for module, name, value in ((release, 'compatibility', lambda: 'd' * 64),
                                    (release, 'source_hash', lambda: 'e' * 64),
                                    (release, 'builder_inputs', lambda: {'locked': True}),
                                    (runtime, 'STATE', self.state), (develop, 'STATE', self.state),
                                    (develop, 'DEV', self.dev)):
            item = patch.object(module, name, value)
            item.start(); self.addCleanup(item.stop)

    def test_no_fictitious_published_release(self):
        self.assertEqual(json.loads(release.DEFAULT.read_text()), {'schema_version': 1, 'release': None})
        with self.assertRaisesRegex(ValueError, 'no published release'):
            release.read()

    def test_valid_complete_release(self):
        data = manifest()
        self.assertEqual(release.validate(data, require_source=True), data['release'])

    def test_missing_nf_or_extra_database_or_mutable_ref_rejected(self):
        data = manifest()
        for mutation in ('missing', 'database', 'tag', 'path', 'credential', 'platform', 'protocol'):
            bad = copy.deepcopy(data)
            spec = bad['release']
            if mutation == 'missing':
                spec['nf_images'].pop('free5gc-smf')
            elif mutation == 'database':
                spec['nf_images']['db'] = image('mongo')
            elif mutation in ('tag', 'path', 'credential'):
                spec['runtime']['reference'] = {'tag': 'ghcr.io/example/runtime:latest',
                    'path': 'ghcr.io/example/../runtime@' + A,
                    'credential': 'ghcr.io/user:password@example/runtime@' + A}[mutation]
            elif mutation == 'platform':
                spec['platform'] = 'linux/arm64'
            else:
                spec['dashboard_snapshot_protocol'] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                release.validate(bad)

    def test_source_mismatch_blocks_install_but_not_development(self):
        data = manifest(); data['release']['source_sha256'] = 'f' * 64
        release.validate(data)
        with self.assertRaisesRegex(ValueError, 'Go source differs'):
            release.validate(data, require_source=True)

    def test_orchestration_and_builder_mismatch_always_fail(self):
        for field in ('compatibility_sha256', 'builder'):
            data = manifest()
            if field == 'builder':
                data['release']['builder']['inputs'] = {'different': True}
            else:
                data['release'][field] = 'f' * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                release.validate(data)

    def test_mongo_is_direct_locked_upstream(self):
        data = manifest(); data['release']['database'] = image('mongo')['reference']
        with self.assertRaisesRegex(ValueError, 'upstream'):
            release.validate(data)

    def test_duplicate_keys_and_oversized_files_fail(self):
        path = self.root / 'manifest.json'
        path.write_text('{"schema_version": 1, "release": null, "release": {}}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            release.read(path)
        path.write_text(' ' * (128 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, 'size limit'):
            release.read(path)

    def test_cached_verified_image_does_not_pull(self):
        with patch.object(release, 'inspect_image', return_value=A), patch.object(release, 'run') as run:
            self.assertEqual(release.acquire(image('runtime')), A)
        run.assert_not_called()

    def test_absent_image_pulled_by_digest_and_checked_again(self):
        spec = image('runtime')
        with patch.object(release, 'inspect_image', side_effect=[subprocess.CalledProcessError(1, 'inspect'), A]), \
             patch.object(release, 'run') as run:
            self.assertEqual(release.acquire(spec), A)
        run.assert_called_once_with(['docker', 'pull', '--platform', 'linux/amd64', spec['reference']])

    def test_wrong_cached_identity_is_not_silently_replaced(self):
        with patch.object(release, 'inspect_image', side_effect=ValueError('identity')), \
             patch.object(release, 'run') as run, self.assertRaises(ValueError):
            release.acquire(image('runtime'))
        run.assert_not_called()

    def test_actual_inspection_checks_id_platform_and_repo_digest(self):
        spec = image('runtime')
        good = {'Id': A, 'Os': 'linux', 'Architecture': 'amd64', 'RepoDigests': [spec['reference']]}
        with patch.object(release, 'output', return_value=json.dumps([good])):
            self.assertEqual(release.inspect_image(spec), A)
        for key, value in [('Id', B), ('Os', 'windows'), ('Architecture', 'arm64'), ('RepoDigests', [])]:
            with self.subTest(key=key), patch.object(release, 'output', return_value=json.dumps([{**good, key: value}])), \
                 self.assertRaises(ValueError):
                release.inspect_image(spec)

    def install_context(self):
        for module, name, kwargs in ((runtime, 'checkout', {'return_value': self.root}),
                                    (runtime, 'render', {}), (release, 'run', {}),
                                    (runtime, 'database_selection', {'return_value': {'image': manifest()['release']['database']}})):
            item = patch.object(module, name, **kwargs)
            mocked = item.start(); self.addCleanup(item.stop)
            setattr(self, name, mocked)

    def test_initial_install_atomic_and_never_acquires_builder(self):
        self.install_context()
        develop.atomic(self.dev / 'overrides.json', {'free5gc-smf': C})
        develop.atomic(self.dev / 'history.json', [{'retained': True}])
        with patch.object(release, 'acquire', side_effect=lambda value: value['image_id']) as acquire:
            release.install({}, manifest())
        self.assertEqual(acquire.call_count, 12)
        self.assertFalse(any(call.args[0] == image('builder', C) for call in acquire.call_args_list))
        selected = json.loads((self.state / 'candidate.json').read_text())
        self.assertEqual(selected['nf_images']['free5gc-smf'], B)
        self.assertEqual(self.render.call_args.args[4]['free5gc-smf'], C)
        self.assertEqual(develop.overrides(), {'free5gc-smf': C})
        self.assertEqual(develop.state_file('history.json', []), [{'retained': True}])
        self.database_selection.assert_called_once_with(create=False)
        self.assertFalse((self.dev / 'builder.json').exists())

    def test_last_pull_or_compose_failure_never_adopts_partial_baseline(self):
        self.install_context()
        for failure in ('pull', 'compose'):
            self.run.side_effect = ValueError('compose') if failure == 'compose' else None
            values = [A] * 11 + [ValueError('last pull')] if failure == 'pull' else [A] * 12
            with patch.object(release, 'acquire', side_effect=values), self.assertRaises(ValueError):
                release.install({}, manifest())
            self.assertFalse((self.state / 'candidate.json').exists())

    def test_existing_baseline_never_replaced(self):
        develop.atomic(self.state / 'candidate.json', {'image_id': B})
        with patch.object(release, 'acquire') as acquire, self.assertRaisesRegex(ValueError, 'existing baseline'):
            release.install({}, manifest())
        acquire.assert_not_called()
        self.assertEqual(json.loads((self.state / 'candidate.json').read_text()), {'image_id': B})

    def test_same_release_can_retry_without_losing_overrides(self):
        self.install_context()
        with patch.object(release, 'acquire', side_effect=lambda value: value['image_id']):
            release.install({}, manifest())
            develop.atomic(self.dev / 'overrides.json', {'dashboard': C})
            release.install({}, manifest())
        self.assertEqual(develop.overrides(), {'dashboard': C})

    def test_pending_development_or_database_blocks_pulls(self):
        for path in (self.dev / 'pending.json', self.state / 'database/pending.json'):
            develop.atomic(path, {})
            with patch.object(release, 'acquire') as acquire, self.assertRaisesRegex(ValueError, 'unfinished'):
                release.install({}, manifest())
            acquire.assert_not_called()
            path.unlink()

    def test_old_database_is_not_automatically_migrated(self):
        with patch.object(runtime, 'database_selection', return_value={'image': 'legacy'}), \
             patch.object(release, 'acquire') as acquire, self.assertRaisesRegex(ValueError, 'no automatic'):
            release.install({}, manifest())
        acquire.assert_not_called()

    def test_release_builder_is_on_demand_and_records_provenance(self):
        data = manifest()
        candidate = {'release_manifest': data, 'release_manifest_sha256': release.fingerprint(data)}
        develop.atomic(self.state / 'candidate.json', candidate)
        with patch.object(release, 'acquire', return_value=C) as acquire, patch.object(develop, 'run') as run:
            result = develop.builder()
        acquire.assert_called_once_with(data['release']['builder']['image'])
        run.assert_not_called()
        self.assertEqual(result['reference'], image('builder', C)['reference'])
        self.assertEqual(develop.state_file('builder.json', {})['image_id'], C)

    def test_missing_builder_does_not_silently_compile(self):
        data = manifest(); data['release']['builder'] = None
        develop.atomic(self.state / 'candidate.json', {'release_manifest': data,
                      'release_manifest_sha256': release.fingerprint(data)})
        with patch.object(develop, 'run') as run, self.assertRaisesRegex(ValueError, '--local-builder'):
            develop.builder()
        run.assert_not_called()

    def test_changed_stored_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            release.release_builder({'release_manifest': manifest(), 'release_manifest_sha256': 'bad'}, {'locked': True})

    def test_explicit_local_builder_uses_local_cache_not_release(self):
        data = manifest(); data['release']['builder'] = None
        develop.atomic(self.state / 'candidate.json', {'release_manifest': data})
        develop.atomic(self.dev / 'builder.json', {'inputs': {'locked': True}, 'image_id': B})
        with patch.object(release, 'release_builder') as remote, patch.object(develop, 'output', return_value=B):
            self.assertEqual(develop.builder(local=True)['image_id'], B)
        remote.assert_not_called()

    def test_unpublished_first_up_does_not_create_or_prepare_vm(self):
        with patch.object(sys, 'argv', ['lab', 'up']), patch.object(launcher, 'VM') as vm:
            vm.return_value.state = self.root / 'absent'
            with self.assertRaisesRegex(ValueError, 'no published release'):
                launcher.main()
        vm.return_value.ensure.assert_not_called()
        vm.return_value.runtime.assert_not_called()

    def test_baseline_guard_runs_before_guest_preparation(self):
        with patch.object(launcher, 'VM') as vm:
            vm.runtime.side_effect = ValueError('existing baseline')
            with self.assertRaisesRegex(ValueError, 'existing baseline'):
                launcher.start(vm, release_data=manifest())
        vm.runtime.assert_called_once_with('check-release')

    def test_prebuilt_and_existing_start_paths_never_build(self):
        for data in (None, manifest()):
            with patch.object(launcher, 'VM') as vm, patch.object(launcher, 'Dashboard'):
                launcher.start(vm, release_data=data)
            self.assertEqual([call.args for call in vm.runtime.call_args_list],
                             [('prepare',), ('up',)] if data is None else
                             [('check-release',), ('prepare',), ('install-release',), ('up',)])

    def test_prebuilt_prepare_does_not_download_application_toolchains(self):
        import inspect
        code = inspect.getsource(runtime.prepare)
        self.assertNotIn("['toolchains']['go']", code)
        self.assertNotIn('prepare_build_tools', code)
        self.assertNotIn("'cmake'", code)


class SourceFingerprintTests(unittest.TestCase):
    def test_source_hash_matches_editable_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = develop.snapshot('mup-controller', Path(directory) / 'source.tar')
        self.assertEqual(release.source_hash(), metadata['source_sha256'])

    def test_inputs_reproducible_without_git_history(self):
        self.assertEqual(release.compatibility(), release.compatibility())
        self.assertEqual(release.builder_inputs()['toolchains']['go'], release.locks()['toolchains']['go'])


class CandidateCITests(unittest.TestCase):
    def test_run_id_is_not_a_path_or_shell_argument(self):
        for value in ('../../x', '1;id-1', '123', '-1', 'a-2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ci.profile(value)
        self.assertEqual(ci.profile('123-2')['vm']['name'], 'mup-ci-123-2')

    def test_workflows_have_no_publication_authority(self):
        import yaml
        for name in ('compact-candidate.yml', 'compact-release-verify.yml'):
            path = ROOT / '.github/workflows' / name
            spec = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            self.assertEqual(spec['permissions'], {'contents': 'read'})
            self.assertEqual(set(spec['on']), {'workflow_dispatch'})
            self.assertFalse(any('permissions' in job for job in spec['jobs'].values()))
        candidate = yaml.load((ROOT / '.github/workflows/compact-candidate.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertIn('github.event.repository.default_branch', candidate['jobs']['candidate']['if'])
        self.assertIn('COMPACT_CANDIDATE_CI', candidate['jobs']['candidate']['if'])
        self.assertEqual(candidate['jobs']['candidate']['environment'], 'compact-candidate')

    def test_ci_retains_and_stops_owned_guest_after_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ci, 'ROOT', Path(directory)), \
             patch.object(sys, 'argv', ['compact_ci.py', '123-2']):
            calls = []
            def command(args):
                calls.append(args[3:])
                if args[3:] == ['up', '--build']:
                    owner = Path(directory) / '.lab/mup-ci-123-2/owner.json'
                    owner.parent.mkdir(); owner.write_text('{}')
                    raise ValueError('failed startup')
            with patch.object(ci, 'run', side_effect=command), self.assertRaisesRegex(ValueError, 'failed startup'):
                ci.main()
            self.assertEqual(calls, [['doctor'], ['up', '--build'], ['down']])
            self.assertTrue((Path(directory) / '.lab/mup-ci-123-2/owner.json').exists())
            with patch.object(ci, 'run') as run, self.assertRaisesRegex(ValueError, 'already exists'):
                ci.main()
            run.assert_not_called()

    def test_doctor_failure_does_not_stop_unowned_resources(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ci, 'ROOT', Path(directory)), \
             patch.object(sys, 'argv', ['compact_ci.py', '123-2']), \
             patch.object(ci, 'run', side_effect=ValueError('preflight')) as run, \
             self.assertRaisesRegex(ValueError, 'preflight'):
            ci.main()
        self.assertEqual(run.call_count, 1)
