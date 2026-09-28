"""Offline gates for component source preservation and image transactions."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_develop as develop

A, B, C = ('sha256:' + character * 64 for character in 'abc')


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dev = self.root / 'development'
        active = patch.object(develop, 'DEV', self.dev)
        active.start(); self.addCleanup(active.stop)

    def test_all_deployed_nfs_have_locked_sources_and_no_mongodb_build(self):
        for name in develop.NF_NAMES:
            spec = develop.source_spec('free5gc-' + name)
            self.assertRegex(spec['commit'], '^[a-f0-9]{40}$')
            self.assertTrue(spec['repository'].startswith('https://github.com/free5gc/'))
        self.assertNotIn('db', develop.COMPONENTS)
        self.assertIn('-o /out/smf ./cmd/main.go', develop.build_script('free5gc-smf'))

    def test_clean_host_installs_git_for_external_source_customization(self):
        installer = (ROOT / 'scripts/compact-deps.sh').read_text()
        self.assertIn('curl git iproute2', installer)
        self.assertIn("'git'", (ROOT / 'scripts/compact_vm.py').read_text())

    def test_clean_runtime_is_explicit_and_only_for_nfs(self):
        with self.assertRaisesRegex(ValueError, 'only for free5GC'):
            develop.host_rebuild(None, 'vinbero', clean_runtime=True)
        with self.assertRaisesRegex(ValueError, 'only for free5GC'):
            develop.rebuild({}, 'mup-controller', 'a' * 32, clean_runtime=True)
        recipe = (ROOT / 'containers/compact-nf.Dockerfile').read_text()
        self.assertIn('FROM ${RUNTIME_BASE}', recipe)
        self.assertIn('COPY payload/free5gc/ /free5gc/', recipe)
        self.assertNotIn('FROM ${PARENT}', recipe)
        self.assertNotIn('COPY cert', recipe)

    def test_private_runtime_paths_are_not_build_inputs(self):
        for name in ('../outside', '/absolute', './bad', 'source/../../bad', '.env',
                     'frontend/.env.local', '.git/config', 'secrets/value', 'test.key',
                     'test.pem', 'trace.pcap', 'node_modules/a.js', 'a.local.yml', '.', ''):
            self.assertFalse(develop.safe_source_name(name), name)
        self.assertTrue(develop.safe_source_name('frontend/yarn.lock'))
        self.assertTrue(develop.safe_source_name('internal/changed.go'))

    def test_existing_dirty_worktree_is_never_reset_or_checked_out(self):
        worktree = self.root / 'worktrees/vinbero'
        worktree.mkdir(parents=True)
        (worktree / 'custom.go').write_text('local edit')
        record = self.root / '.lab/source-records/vinbero.json'
        record.parent.mkdir(parents=True)
        spec = develop.source_spec('vinbero')
        record.write_text(json.dumps(spec))
        with patch.object(develop, 'ROOT', self.root), \
             patch.object(develop, 'output', return_value=spec['repository']), \
             patch.object(develop, 'run') as run:
            self.assertEqual(develop.source('vinbero'), worktree)
        self.assertEqual((worktree / 'custom.go').read_text(), 'local edit')
        self.assertEqual(run.call_count, 1)
        self.assertIn('merge-base', run.call_args.args[0])

    def test_snapshot_includes_new_code_not_private_files_and_preserves_source(self):
        for name, content in [('go.mod', 'module example'), ('go.sum', ''),
                              ('internal/changed.go', 'package changed'), ('internal/secret.key', 'private')]:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        archive = self.root / 'build' / 'source.tar'
        with patch.object(develop, 'source', return_value=self.root), patch.object(develop, 'output', return_value='local-head'):
            manifest = develop.snapshot('mup-controller', archive)
        self.assertIn('internal/changed.go', manifest['files'])
        self.assertNotIn('internal/secret.key', manifest['files'])
        self.assertEqual((self.root / 'internal/changed.go').read_text(), 'package changed')
        result = develop.unpack(archive.parent, 'mup-controller')
        self.assertEqual(result['source_sha256'], manifest['source_sha256'])
        self.assertEqual((archive.parent / 'source/internal/changed.go').read_text(), 'package changed')

    def test_history_free_source_archive_can_be_customized_and_rebuilt(self):
        (self.root / 'go.mod').write_text('module example')
        (self.root / 'go.sum').write_text('')
        with patch.object(develop, 'source', return_value=self.root), patch.object(develop, 'output') as output:
            record = develop.snapshot('mup-dashboard', self.root / 'build/source.tar')
        output.assert_not_called()
        self.assertIsNone(record['head'])
        self.assertEqual(len(record['source_sha256']), 64)
        self.assertEqual(develop.unpack(self.root / 'build', 'mup-dashboard'), record)

    def test_tar_rejects_traversal_symlinks_and_duplicates_before_extracting(self):
        for names in (['source/../../escape'], ['source/x', 'source/x'], ['symlink']):
            archive = self.root / 'source.tar'
            with tarfile.open(archive, 'w') as output:
                for name in names:
                    info = tarfile.TarInfo(name)
                    if name == 'symlink':
                        info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'
                    output.addfile(info, io.BytesIO(b''))
            with self.assertRaises(ValueError):
                develop.unpack(self.root, 'vinbero')
        self.assertFalse((self.root / 'source').exists())

    def test_builder_has_only_source_output_cache_and_no_host_privileges(self):
        command = develop.builder_command(self.root, A)
        self.assertIn('--read-only', command)
        self.assertIn('--cap-drop=ALL', command)
        self.assertIn('65534:65534', command)
        self.assertIn(str(self.root / 'source') + ':/source:ro', command)
        joined = ' '.join(map(str, command))
        for forbidden in ('docker.sock', '--privileged', '--network=host', 'id_ed25519', 'compact.local.yml'):
            self.assertNotIn(forbidden, joined)

    def test_build_temporary_space_is_disk_backed_without_reassigning_home(self):
        for name in develop.COMPONENTS:
            script = develop.build_script(name)
            self.assertIn('export TMPDIR=/scratch/tmp', script)
            self.assertNotIn('export HOME=', script)
        self.assertIn('YARN_GLOBAL_FOLDER=/cache/yarn-global', develop.build_script('free5gc-webui'))

    def test_failed_compilation_cannot_mutate_selected_images(self):
        path = self.dev / 'builds' / ('a' * 32)
        path.mkdir(parents=True)
        develop.atomic(self.dev / 'overrides.json', {'mupc': A})
        with patch.object(develop, 'unpack', return_value={}), \
             patch.object(develop, 'resolve_image', return_value=A), \
             patch.object(develop, 'builder', return_value={'image_id': B}), \
             patch.object(develop.os, 'chown'), \
             patch.object(develop, 'run', side_effect=subprocess.CalledProcessError(1, 'build')), \
             patch.object(develop, 'activate') as activate:
            with self.assertRaises(subprocess.CalledProcessError):
                develop.rebuild({}, 'mup-controller', 'a' * 32)
        activate.assert_not_called()
        self.assertEqual(develop.overrides(), {'mupc': A})
        self.assertTrue(json.loads((path / 'result.json').read_text())['active_images_unchanged'])

    def test_activation_failure_restores_previous_selection_and_history(self):
        old = {'mupc': A}; new = {'mupc': B}
        path = self.dev / 'builds' / 'test'
        with patch.object(develop, 'deploy', side_effect=[ValueError('bad candidate'), None]) as deploy:
            with self.assertRaisesRegex(ValueError, 'restored and verified'):
                develop.activate({}, old, new, [], [{'component': 'mup-controller'}], path)
        self.assertEqual(deploy.call_count, 2)
        self.assertEqual(develop.overrides(), old)
        self.assertEqual(develop.state_file('history.json', None), [])
        self.assertFalse((self.dev / 'pending.json').exists())

    def test_double_failure_retains_recovery_record(self):
        with patch.object(develop, 'deploy', side_effect=ValueError('failure')):
            with self.assertRaisesRegex(ValueError, 'recovery failed'):
                develop.activate({}, {'tpe': A}, {'tpe': B}, [], [], self.dev / 'builds/test')
        self.assertTrue((self.dev / 'pending.json').exists())
        develop.recover()
        self.assertEqual(develop.overrides(), {'tpe': A})
        self.assertFalse((self.dev / 'pending.json').exists())
        self.assertTrue(list(self.dev.glob('recovered-*.json')))

    def test_success_records_history_and_rollback_preserves_other_components(self):
        event = {'component': 'vinbero', 'before': {'tpe': None, 'npe': None}, 'after': {'tpe': B, 'npe': B}}
        with patch.object(develop, 'deploy'):
            develop.activate({}, {'dashboard': A}, {'dashboard': A, 'tpe': B, 'npe': B}, [], [event], self.dev / 'builds/test')
            develop.rollback({}, 'vinbero')
        self.assertEqual(develop.overrides(), {'dashboard': A})
        self.assertEqual(develop.state_file('history.json', None), [])

    def test_shared_service_conflict_requires_reverse_rollback_order(self):
        develop.atomic(self.dev / 'history.json', [{'component': 'mup-controller',
            'before': {'mupc': None}, 'after': {'mupc': B}}])
        develop.atomic(self.dev / 'overrides.json', {'mupc': C})
        with self.assertRaisesRegex(ValueError, 'later component'):
            develop.rollback({}, 'mup-controller')
        self.assertEqual(develop.overrides(), {'mupc': C})

    def test_override_rejects_db_and_mutable_tags(self):
        for values in ({'db': A}, {'tpe': 'latest'}, {'dn': A}):
            develop.atomic(self.dev / 'overrides.json', values)
            with self.assertRaises(ValueError):
                develop.overrides()
