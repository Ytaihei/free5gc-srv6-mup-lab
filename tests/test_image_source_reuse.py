"""Explicit private caches are revalidated, copied, bounded and never approval."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import image_sources as sources
from test_image_sources import DSC, ENTRY, PAYLOAD, SPEC, URL

SOURCE = {'name': 'example', 'version': '1.0',
          'key': hashlib.sha256(b'example\x001.0').hexdigest()}


def fixture(root):
    old = root / 'old'; old.mkdir()
    location = old / SOURCE['key']; location.mkdir()
    blobs = old / 'source-blobs'; blobs.mkdir()
    urls = json.dumps([URL.replace('.tar.xz', '.dsc'), URL]).encode()
    with patch.object(sources, 'metadata', side_effect=[json.dumps({'entries': [ENTRY]}).encode(), urls, DSC]), \
         patch.object(sources, 'response', return_value=io.BytesIO(PAYLOAD)):
        record = sources.collect_source(SOURCE, location, blobs, {'remaining': 1000})
    record['key'] = SOURCE['key']
    data = {'schema_version': 1, 'complete': True, 'collection_complete': False,
            'inputs': {'sources': [SOURCE]}, 'sources': [record, {'key': 'b' * 64, 'status': 'collection-failed'}]}
    (old / 'sources.json').write_text(json.dumps(data))
    return old


class SourceReuseTests(unittest.TestCase):
    def test_completed_loop_with_gaps_reuses_only_verified_successful_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = fixture(root)
            indexed, manifests = sources.cache_index([old])
            self.assertEqual(list(indexed), [SOURCE['key']])
            output = root / 'new'; output.mkdir()
            blobs = output / 'source-blobs'; blobs.mkdir()
            budget = {'remaining': 1000}
            with patch.object(sources, 'response') as network:
                result = sources.reuse_source(SOURCE, indexed[SOURCE['key']], output, blobs, budget)
                network.assert_not_called()
            self.assertFalse(result['signature_verified'])
            self.assertFalse(result['distribution_approved'])
            self.assertEqual(result['reused_from_collection_sha256'], manifests[0])
            self.assertEqual(budget['reused_bytes'], len(PAYLOAD))
            self.assertEqual(budget['remaining'], 1000 - len(PAYLOAD))
            self.assertNotEqual((blobs / SPEC['sha256']).stat().st_ino,
                                (old / 'source-blobs' / SPEC['sha256']).stat().st_ino)
            self.assertEqual((blobs / SPEC['sha256']).stat().st_mode & 0o777, 0o600)

    def test_incomplete_malformed_duplicate_and_changed_input_caches_are_rejected(self):
        for change in ['incomplete', 'malformed', 'duplicate', 'changed']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                old = fixture(Path(temp)); path = old / 'sources.json'; data = json.loads(path.read_text())
                if change == 'incomplete': data['complete'] = False
                if change == 'malformed': data.pop('inputs')
                if change == 'duplicate': data['inputs']['sources'].append(SOURCE)
                if change == 'changed': data['inputs']['sources'][0]['version'] = '1.1'
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError): sources.cache_index([old])

    def test_modified_evidence_or_blob_is_not_silently_refetched(self):
        for target in ['publication.json', 'source-file-urls.json', 'source.dsc', 'blob']:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); old = fixture(root); indexed, _ = sources.cache_index([old])
                path = old / 'source-blobs' / SPEC['sha256'] if target == 'blob' else old / SOURCE['key'] / target
                path.write_bytes(b'changed')
                output = root / 'new'; output.mkdir()
                with patch.object(sources, 'response') as network, self.assertRaises(ValueError):
                    sources.reuse_source(SOURCE, indexed[SOURCE['key']], output, output, {'remaining': 1000})
                network.assert_not_called()
                self.assertFalse((output / SPEC['sha256']).exists())

    def test_source_cache_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = fixture(root)
            link = root / 'link'; link.symlink_to(old, target_is_directory=True)
            with self.assertRaises(ValueError): sources.cache_index([link])
            indexed, _ = sources.cache_index([old])
            path = old / SOURCE['key'] / 'source.dsc'; path.rename(path.with_suffix('.saved'))
            path.symlink_to(path.with_suffix('.saved'))
            with self.assertRaises(ValueError):
                sources.reuse_source(SOURCE, indexed[SOURCE['key']], root, root, {'remaining': 1000})

    def test_copy_budget_and_blob_symlinks_fail_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'blob'; source.write_bytes(PAYLOAD)
            with patch.object(sources, 'response') as network:
                with self.assertRaises(ValueError):
                    sources.download(URL, root, SPEC, {'remaining': 1}, source)
                link = root / 'link'; link.symlink_to(source)
                with self.assertRaises(ValueError):
                    sources.download(URL, root, SPEC, {'remaining': 1000}, link)
                network.assert_not_called()

    def test_collection_records_reuse_failure_without_network_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); old = fixture(root)
            (old / SOURCE['key'] / 'source.dsc').write_bytes(b'changed')
            with patch.object(sources, 'ROOT', root), \
                 patch.object(sources, 'inventory', return_value={'unresolved': [], 'sources': [SOURCE]}), \
                 patch.object(sources, 'response') as network, self.assertRaises(ValueError):
                sources.collect(Path('/unused'), 'all', 1000, all_layers=True, reuse=[old])
            network.assert_not_called()
            path, = root.glob('.lab/distro-sources/*/sources.json')
            result = json.loads(path.read_text())
            self.assertTrue(result['complete'])
            self.assertFalse(result['collection_complete'])
            self.assertFalse(result['publication_approved'])
            self.assertEqual(result['sources'][0]['status'], 'collection-failed')


if __name__ == '__main__':
    unittest.main()
