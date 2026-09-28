"""Saved build snapshots must match immutable image payloads, not a checkout."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import image_build_sources as builds
from test_image_source_layers import archive


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_fixture(path):
    files = {'main.go': b'fixture source', 'frontend/yarn.lock': b'fixture lock'}
    specs = {name: {'sha256': sha(raw), 'mode': 0o644} for name, raw in files.items()}
    manifest = {'component': 'free5gc-webui', 'files': specs, 'source_sha256': sha(json.dumps(specs, sort_keys=True).encode())}
    raw = archive([('manifest.json', json.dumps(manifest).encode()),
                   *[('source/' + name, data) for name, data in files.items()]])
    (path / 'source.tar').write_bytes(raw)
    (path / 'source-manifest.json').write_text(json.dumps(manifest))
    return {'component': 'free5gc-webui', 'source_sha256': manifest['source_sha256']}


def image_fixture(path, layers):
    names = ['layer' + str(i) for i in range(len(layers))]
    path.write_bytes(archive([('manifest.json', json.dumps([{'Layers': names}]).encode()), *zip(names, layers)]))


class BuildSourceTests(unittest.TestCase):
    def test_source_snapshot_hashes_and_declared_frontend_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); record = source_fixture(root)
            result = builds.source_proof(root, record)
            self.assertEqual(result['files'], 2)
            self.assertEqual(result['declared_frontend_inputs'], {'frontend/yarn.lock': sha(b'fixture lock')})
            self.assertFalse(result['effective_frontend_dependency_archives_verified'])

    def test_changed_source_bytes_manifest_or_build_record_fail(self):
        for change in ['bytes', 'manifest', 'record']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); record = source_fixture(root)
                if change == 'bytes':
                    path = root / 'source.tar'; path.write_bytes(path.read_bytes().replace(b'fixture source', b'changed source'))
                elif change == 'manifest':
                    (root / 'source-manifest.json').write_text('{}')
                else:
                    record['source_sha256'] = 'a' * 64
                with self.assertRaises(ValueError): builds.source_proof(root, record)

    def test_links_traversal_duplicates_and_size_limits_fail(self):
        for entries in [[('source/link', None)], [('source/../outside', b'x')],
                        [('manifest.json', b'{}'), ('manifest.json', b'{}')]]:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); record = source_fixture(root)
                (root / 'source.tar').write_bytes(archive(entries))
                with self.assertRaises(ValueError): builds.source_proof(root, record)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); record = source_fixture(root)
            with patch.object(builds, 'MAX_SOURCE', 1), self.assertRaises(ValueError):
                builds.source_proof(root, record)

    def test_payload_checks_last_layer_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'image.tar'
            image_fixture(image, [archive([('free5gc/webui', b'old')]), archive([('free5gc/webui', b'new')])])
            self.assertEqual(builds.payload_proof(image, {'webui': sha(b'new')}), 1)
            with self.assertRaises(ValueError): builds.payload_proof(image, {'webui': sha(b'old')})

    def test_whiteouts_links_and_directory_replacement_invalidate_lower_payload(self):
        for item in [('free5gc/.wh.webui', b''), ('free5gc/.wh..wh..opq', b''),
                     ('.wh.free5gc', b''), ('free5gc/webui', None), ('free5gc', None)]:
            with self.subTest(item=item), tempfile.TemporaryDirectory() as temp:
                image = Path(temp) / 'image.tar'
                image_fixture(image, [archive([('free5gc/webui', b'old')]), archive([item])])
                with self.assertRaises(ValueError): builds.payload_proof(image, {'webui': sha(b'old')})

    def test_whiteout_and_new_file_in_same_layer_keeps_new_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'image.tar'
            image_fixture(image, [archive([('free5gc/webui', b'old')]),
                                  archive([('free5gc/.wh..wh..opq', b''), ('free5gc/webui', b'new')])])
            self.assertEqual(builds.payload_proof(image, {'webui': sha(b'new')}), 1)

    def test_ambiguous_ancestor_in_same_layer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'image.tar'
            image_fixture(image, [archive([('free5gc', None), ('free5gc/webui', b'new')])])
            with self.assertRaises(ValueError): builds.payload_proof(image, {'webui': sha(b'new')})

    def test_fetch_rejects_unrelated_guest_paths_before_ssh(self):
        vm = Mock()
        for name in ['../candidate.json', '/etc/shadow', 'development/id_ed25519', 'bootstrap/abc/nfs/def/build.json']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                builds.fetch(vm, name, Path('/unused'))
        vm.ssh.assert_not_called()

    def test_wrong_image_id_fails_before_source_or_payload_work(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / 'build.json').write_text(json.dumps({'component': 'free5gc-webui', 'image_id': 'different', 'clean_runtime': True}))
            with patch.object(builds, 'source_proof') as source, self.assertRaises(ValueError):
                builds.verify_build(path, 'free5gc-webui', 'expected', Path('/unused'))
            source.assert_not_called()


if __name__ == '__main__':
    unittest.main()
