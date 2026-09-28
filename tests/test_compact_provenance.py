"""Bootstrap and frontend evidence belong to the actual isolated build."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import compact_develop as develop
import compact_runtime as runtime
import image_build_sources as collect


def frontend_fixture(path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('node_modules/example/LICENSE', 'MIT fixture')
        archive.writestr('node_modules/example/index.js', 'fixture')
    for name, raw in [('frontend-locks/package.json', b'{}'), ('frontend-locks/yarn.lock', b'locked'),
                      ('frontend-locks/.yarnrc.yml', b'nodeLinker: node-modules'),
                      ('frontend-cache/@scope-example-npm-1.0.0.zip', buffer.getvalue()), ('go.mod', b'module example')]:
        target = path / 'provenance' / name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)


class ProvenanceTests(unittest.TestCase):
    def test_frontend_cache_is_private_per_build_and_install_remains_immutable(self):
        script = develop.build_script('free5gc-webui')
        self.assertIn('YARN_CACHE_FOLDER=/provenance/frontend-cache', script)
        self.assertIn('YARN_ENABLE_GLOBAL_CACHE=0', script)
        self.assertIn('install --immutable', script)
        self.assertIn('cp package.json yarn.lock .yarnrc.yml /provenance/frontend-locks/', script)

    def test_frontend_archives_and_effective_locks_are_recorded_without_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); frontend_fixture(root)
            record = develop.provenance(root, 'free5gc-webui')
            self.assertEqual(set(record['effective_module_locks']), {'go.mod'})
            self.assertEqual(len(record['frontend_sources']), 4)
            self.assertEqual((root / 'frontend-sources.tar').stat().st_mode & 0o777, 0o600)
            proof = collect.frontend_proof(root, {'component': 'free5gc-webui', **record})
            self.assertTrue(proof['effective_inputs_collected'])
            self.assertEqual(proof['dependency_archives'], 1)
            self.assertEqual(proof['embedded_notice_paths'], 1)
            self.assertFalse(proof['license_review_complete'])

    def test_frontend_unexpected_symlink_missing_cache_or_wrong_component_fails(self):
        for change in ['symlink', 'unexpected', 'missing', 'component']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); frontend_fixture(root)
                if change == 'symlink': (root / 'provenance/link').symlink_to('go.mod')
                if change == 'unexpected': (root / 'provenance/secret.txt').write_text('fixture')
                if change == 'missing': (root / 'provenance/frontend-cache/@scope-example-npm-1.0.0.zip').unlink()
                with self.assertRaises(ValueError):
                    develop.provenance(root, 'free5gc-smf' if change == 'component' else 'free5gc-webui')

    def test_large_icon_archive_is_supported_but_member_limit_remains_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); frontend_fixture(root)
            target = root / 'provenance/frontend-cache/@scope-example-npm-1.0.0.zip'
            with zipfile.ZipFile(target, 'w') as archive:
                archive.writestr('node_modules/example/LICENSE', 'MIT fixture')
                for index in range(32000):
                    archive.writestr(f'node_modules/example/icon-{index}.js', 'fixture')
            record = {'component': 'free5gc-webui', **develop.provenance(root, 'free5gc-webui')}
            self.assertTrue(collect.frontend_proof(root, record)['effective_inputs_collected'])
            with patch.object(collect, 'MAX_FRONTEND_ZIP_ENTRIES', 32000):
                with self.assertRaisesRegex(ValueError, 'ZIP exceeds bounds'):
                    collect.frontend_proof(root, record)

    def test_archive_or_record_tampering_is_detected(self):
        for change in ['archive', 'record']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); frontend_fixture(root)
                record = {'component': 'free5gc-webui', **develop.provenance(root, 'free5gc-webui')}
                if change == 'archive':
                    target = root / 'frontend-sources.tar'
                    target.write_bytes(target.read_bytes().replace(b'locked', b'edited'))
                else:
                    record['frontend_sources']['frontend-locks/yarn.lock']['sha256'] = 'a' * 64
                with self.assertRaises(ValueError): collect.frontend_proof(root, record)

    def test_legacy_records_are_not_silently_claimed_to_have_sources(self):
        self.assertFalse(collect.frontend_proof(Path('/unused'), {})['effective_inputs_collected'])
        result = collect.collect_common(Path('/unused'), Mock(), Path('/unused'), {})
        self.assertFalse(result['collection_complete'])
        self.assertEqual(result['status'], 'missing-build-time-snapshots')

    def test_common_payload_prefix_checks_actual_image_location(self):
        from test_image_source_layers import archive
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); image = root / 'image.tar'
            layer = archive([('usr/local/bin/vinbero', b'binary')])
            image.write_bytes(archive([('manifest.json', b'[{"Layers":["layer"]}]'), ('layer', layer)]))
            values = {'vinbero': hashlib.sha256(b'binary').hexdigest()}
            self.assertEqual(collect.payload_proof(image, values, 'usr/local/bin'), 1)
            with self.assertRaises(ValueError): collect.payload_proof(image, values)
            with self.assertRaises(ValueError): collect.payload_proof(image, values, '../outside')

    def test_common_bootstrap_uses_snapshots_builder_payloads_and_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); state = root / 'runtime'; repo = root / 'source'
            (repo / 'containers').mkdir(parents=True)
            (repo / 'containers/compact-runtime.Dockerfile').write_text('FROM fixture\n')
            (repo / 'LICENSE').write_text('license')
            (repo / 'THIRD_PARTY_NOTICES.md').write_text('notices')
            (repo / 'go.mod').write_text('github.com/osrg/gobgp/v4 v4.8.0')
            names = []
            def payload(name, path, **kwargs):
                self.assertEqual(kwargs, {'local_builder': True})
                names.append(name); (path / 'output').mkdir(parents=True)
                file = path / 'output' / name; file.write_text(name)
                return {'artifacts': {name: develop.digest(file)}}
            with patch.object(runtime, 'ROOT', repo), patch.object(runtime, 'STATE', state), \
                 patch.object(develop, 'DEV', state / 'development'), \
                 patch.object(runtime, 'prepare'), patch.object(runtime, 'prepare_build_tools'), \
                 patch.object(runtime, 'checkout', return_value=repo), patch.object(develop, 'snapshot') as snapshot, \
                 patch.object(develop, 'build_payload', side_effect=payload), patch.object(runtime, 'run') as run, \
                 patch.object(runtime, 'output', return_value='sha256:' + 'a' * 64), \
                 patch.object(runtime, 'complete_candidate') as complete:
                runtime.build({})
            self.assertEqual(set(names), set(collect.COMMON))
            self.assertEqual(snapshot.call_count, 6)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][:2], ['docker', 'build'])
            manifest = complete.call_args.args[2]
            self.assertEqual(set(manifest['common_build_records']), set(collect.COMMON))
            self.assertEqual(develop.digest(state / manifest['common_runtime_recipe']), manifest['common_runtime_recipe_sha256'])
            self.assertEqual(len(list(state.glob('bootstrap/*/LICENSE'))), 1)


if __name__ == '__main__':
    unittest.main()
