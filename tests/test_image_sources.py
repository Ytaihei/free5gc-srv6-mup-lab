"""Exact distro-source collection stays private, bounded and artifact-bound."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import image_sources as sources

PAYLOAD = b'source archive fixture, never extracted'
SPEC = {'size': len(PAYLOAD), 'sha256': hashlib.sha256(PAYLOAD).hexdigest()}
URL = 'https://launchpad.net/ubuntu/+archive/primary/+sourcefiles/example/1.0/example.tar.xz'
DSC = (f'Format: 3.0 (native)\nSource: example\nVersion: 1.0\nChecksums-Sha256:\n'
       f' {SPEC["sha256"]} {SPEC["size"]} example.tar.xz\n').encode()
ENTRY = {'source_package_name': 'example', 'source_package_version': '1.0',
         'archive_link': sources.API, 'distro_series_link': sources.SERIES,
         'self_link': sources.API + '/+sourcepub/123', 'status': 'Published'}


class ImageSourceTests(unittest.TestCase):
    def test_urls_allow_only_https_official_infrastructure(self):
        for url in [URL, sources.API, 'https://launchpadlibrarian.net/123/a',
                    'https://i123.launchpadlibrarian.net/a']:
            self.assertEqual(sources.official_url(url), url)
        for url in ['http://launchpad.net/a', 'https://user:pass@launchpad.net/a',
                    'https://launchpad.net:444/a', 'https://launchpad.net.evil.test/a',
                    'https://evil.launchpad.net/a', 'https://127.0.0.1/a',
                    'file:///etc/passwd', URL + '#secret', URL + '\n', None]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                sources.official_url(url)

    def test_offsite_redirect_is_rejected_before_request(self):
        with self.assertRaises(ValueError):
            sources.OfficialRedirect().redirect_request(
                urllib.request.Request(URL), None, 302, 'Found', {}, 'https://example.com/a')

    def test_metadata_is_size_bounded(self):
        with patch.object(sources, 'MAX_META', 8), patch.object(sources, 'response', return_value=io.BytesIO(b'x' * 9)):
            with self.assertRaises(ValueError):
                sources.metadata(URL)

    def test_roles_separate_runtime_builder_and_upstream(self):
        for roles, expected in [(['builder'], 'builder'), (['db'], 'upstream'),
                                (['bootstrap', 'free5gc-amf'], 'runtime')]:
            self.assertEqual(sources.group(roles), expected)
        for roles in [[], ['unknown'], ['db', 'builder'], ['builder', 'free5gc-amf'], 'db']:
            with self.assertRaises(ValueError):
                sources.group(roles)

    def test_version_preserves_distro_revision_and_independent_source_epoch(self):
        package = {'Version': '13.2.0', 'Release': '7ubuntu1', 'Epoch': 4,
                   'SrcVersion': '1.214ubuntu1'}
        self.assertEqual(sources.package_version(package), '4:13.2.0-7ubuntu1')
        self.assertEqual(sources.package_version(package, 'Src'), '1.214ubuntu1')
        self.assertEqual(sources.package_version({'SrcVersion': '18.1.3', 'SrcRelease': '1ubuntu1',
                                                  'SrcEpoch': 1}, 'Src'), '1:18.1.3-1ubuntu1')
        self.assertEqual(sources.package_version({'Version': '2.3.2', 'Release': '1build1.1'}), '2.3.2-1build1.1')
        for package in [{}, {'Version': '1', 'Epoch': -1}, {'Version': '1', 'Epoch': True},
                        {'Version': '1:1', 'Epoch': 1}, {'Version': '1', 'Release': '../bad'}]:
            with self.assertRaises(ValueError):
                sources.package_version(package)

    def test_descriptor_exact_version_and_checksums(self):
        self.assertEqual(sources.descriptor(DSC, 'example', '1.0'), {'example.tar.xz': SPEC})
        signed = b'-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n' + DSC
        signed += b'\n-----BEGIN PGP SIGNATURE-----\nfixture\n-----END PGP SIGNATURE-----\n'
        self.assertEqual(sources.descriptor(signed.replace(b'\n', b'\r\n'), 'example', '1.0'),
                         {'example.tar.xz': SPEC})
        for name, version in [('other', '1.0'), ('example', '1.1')]:
            with self.assertRaises(ValueError):
                sources.descriptor(DSC, name, version)

    def test_descriptor_rejects_unsafe_names_duplicates_and_incomplete_signatures(self):
        for data in [DSC.replace(b'example.tar.xz', b'../example.tar.xz'),
                     DSC.replace(b'example.tar.xz', b'/example.tar.xz'),
                     DSC + b'source: example\n', DSC.replace(b'Checksums-Sha256', b'Checksums-Sha1'),
                     DSC + DSC.split(b'Checksums-Sha256:\n')[1],
                     b'-----BEGIN PGP SIGNED MESSAGE-----\n\n' + DSC,
                     DSC.replace(str(SPEC['size']).encode(), b'0')]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                sources.descriptor(data, 'example', '1.0')

    def test_publication_queries_exact_noble_source_version(self):
        with patch.object(sources, 'metadata', return_value=json.dumps({'entries': [ENTRY]}).encode()) as fetch:
            entry, proof, raw = sources.publications('example', '1.0')
        self.assertEqual(entry, ENTRY)
        query = fetch.call_args.args[0]
        self.assertIn('exact_match=true', query)
        self.assertIn('version=1.0', query)
        self.assertIn('ubuntu%2Fnoble', query)
        self.assertEqual(proof['sha256'], hashlib.sha256(raw).hexdigest())

    def test_publication_never_substitutes_version_archive_series_or_missing_record(self):
        for changed in [{'source_package_version': '1.1'}, {'archive_link': 'https://ppa.launchpad.net/'},
                        {'distro_series_link': sources.SERIES + '-other'}, {'status': 'Deleted'},
                        {'self_link': 'https://example.com/123'}]:
            with patch.object(sources, 'metadata', return_value=json.dumps({'entries': [{**ENTRY, **changed}]}).encode()):
                with self.assertRaises(ValueError):
                    sources.publications('example', '1.0')
        for data in [{'entries': []}, {'entries': [ENTRY], 'next_collection_link': URL}]:
            with patch.object(sources, 'metadata', return_value=json.dumps(data).encode()), self.assertRaises(ValueError):
                sources.publications('example', '1.0')

    def test_download_hash_size_permissions_and_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); budget = {'remaining': 1000}
            with patch.object(sources, 'response', return_value=io.BytesIO(PAYLOAD)) as fetch:
                sources.download(URL, root, SPEC, budget)
                sources.download(URL, root, SPEC, budget)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(budget['remaining'], 1000 - len(PAYLOAD))
            self.assertEqual((root / SPEC['sha256']).read_bytes(), PAYLOAD)
            self.assertEqual((root / SPEC['sha256']).stat().st_mode & 0o777, 0o600)

    def test_failed_downloads_retain_partial_consume_budget_and_never_become_valid_cache(self):
        for data in [b'wrong', PAYLOAD + b'oversized', b'x' * len(PAYLOAD)]:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp); budget = {'remaining': 1000}
                with patch.object(sources, 'response', return_value=io.BytesIO(data)), self.assertRaises(ValueError):
                    sources.download(URL, root, SPEC, budget)
                self.assertFalse((root / SPEC['sha256']).exists())
                self.assertTrue(list(root.glob('*.partial')))
                self.assertEqual(budget['remaining'], 1000 - len(PAYLOAD))

    def test_parallel_metadata_workers_cannot_race_the_shared_archive_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); budget = {'remaining': len(PAYLOAD)}
            with patch.object(sources, 'response', side_effect=lambda _: io.BytesIO(PAYLOAD)) as fetch:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = [pool.submit(sources.download, URL, root, SPEC, budget) for _ in range(4)]
                    for future in futures:
                        future.result()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(budget['remaining'], 0)
            self.assertEqual((root / SPEC['sha256']).read_bytes(), PAYLOAD)

    def test_worker_count_is_bounded_before_any_work(self):
        with patch.object(sources, 'inventory') as inventory:
            for jobs in [0, 5, -1, True]:
                with self.assertRaises(ValueError):
                    sources.collect(Path('/unused'), 'all', 1000, jobs)
            inventory.assert_not_called()

    def test_bad_cache_symlinks_and_budget_exhaustion_prevent_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / SPEC['sha256']
            with patch.object(sources, 'response') as fetch:
                with self.assertRaises(ValueError):
                    sources.download(URL, root, SPEC, {'remaining': 1})
                target.write_bytes(b'bad cache')
                with self.assertRaises(ValueError):
                    sources.download(URL, root, SPEC, {'remaining': 1000})
                target.unlink(); target.symlink_to(root / 'absent')
                with self.assertRaises(ValueError):
                    sources.download(URL, root, SPEC, {'remaining': 1000})
                fetch.assert_not_called()

    def audit_fixture(self, directory, package=None, os_info=None):
        identifier = 'sha256:' + 'a' * 64
        root = directory / identifier[7:]; root.mkdir()
        report = {'ArtifactType': 'container_image', 'Metadata': {
            'ImageID': identifier, 'DiffIDs': [], 'OS': os_info or {'Family': 'ubuntu', 'Name': '24.04', 'Eosl': False}},
            'Results': [{'Class': 'os-pkgs', 'Type': 'ubuntu', 'Packages': [package or {
                'Name': 'example-bin', 'Version': '1.0', 'SrcName': 'example', 'SrcVersion': '1.0', 'Arch': 'amd64'}]}]}
        (root / 'scan.json').write_text(json.dumps(report))
        (root / 'image.tar').write_bytes(b'archive fixture; verification separately tested')
        (root / 'sbom.cdx.json').write_text('{}')
        item = {'image_id': identifier, 'roles': ['free5gc-amf'], 'complete': True}
        for name, key in [('image.tar', 'archive_sha256'), ('scan.json', 'report_sha256'), ('sbom.cdx.json', 'sbom_sha256')]:
            item[key] = sources.digest(root / name)
        (directory / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'complete': True,
            'selection_unchanged': True, 'database_unchanged': True, 'images': [item]}))
        return identifier, root

    def test_inventory_binds_package_identity_to_saved_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); identifier, _ = self.audit_fixture(root)
            with patch.object(sources, 'verify_archive', return_value={'config_id': identifier, 'diff_ids': []}):
                data = sources.inventory(root, 'runtime')
            self.assertEqual(data['unresolved'], [])
            self.assertEqual(data['sources'][0]['name'], 'example')
            self.assertEqual(data['sources'][0]['binaries'][0]['image_id'], identifier)
            self.assertEqual(data['audit_manifest_sha256'], sources.digest(root / 'manifest.json'))

    def test_inventory_rejects_tampered_artifacts_and_scanner_identity(self):
        for filename in ['image.tar', 'scan.json', 'sbom.cdx.json', None]:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp); _, image = self.audit_fixture(root)
                if filename:
                    (image / filename).write_bytes(b'changed')
                with patch.object(sources, 'verify_archive', return_value={'config_id': 'wrong', 'diff_ids': []}), self.assertRaises(ValueError):
                    sources.inventory(root, 'runtime')

    def test_inventory_records_missing_source_identity_without_binary_version_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identifier, _ = self.audit_fixture(root, package={'Name': 'binary', 'Version': '1.0'})
            with patch.object(sources, 'verify_archive', return_value={'config_id': identifier, 'diff_ids': []}):
                data = sources.inventory(root, 'all')
            self.assertEqual(data['sources'], [])
            self.assertEqual(len(data['unresolved']), 1)

    def test_unknown_os_is_unresolved_and_empty_scope_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); identifier, _ = self.audit_fixture(root, os_info={'Family': 'ubuntu', 'Name': '26.04'})
            with patch.object(sources, 'verify_archive', return_value={'config_id': identifier, 'diff_ids': []}):
                self.assertEqual(len(sources.inventory(root, 'all')['unresolved']), 1)
            with self.assertRaises(ValueError):
                sources.inventory(root, 'builder')

    def test_source_collection_keeps_evidence_without_claiming_signature_or_distribution_approval(self):
        urls = json.dumps([URL.replace('.tar.xz', '.dsc'), URL]).encode()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); blobs = root / 'blobs'; blobs.mkdir()
            with patch.object(sources, 'publications', return_value=(ENTRY, {}, b'{}')), \
                 patch.object(sources, 'metadata', side_effect=[urls, DSC]), \
                 patch.object(sources, 'response', return_value=io.BytesIO(PAYLOAD)):
                result = sources.collect_source({'name': 'example', 'version': '1.0'}, root, blobs, {'remaining': 1000})
            self.assertEqual(result['status'], 'collected-unreviewed')
            self.assertFalse(result['signature_verified'])
            self.assertFalse(result['distribution_approved'])
            self.assertEqual(result['archives'][0]['sha256'], SPEC['sha256'])

    def test_missing_declared_file_is_not_successful_collection(self):
        urls = json.dumps([URL.replace('.tar.xz', '.dsc'), URL + '-extra']).encode()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(sources, 'publications', return_value=(ENTRY, {}, b'{}')), \
                 patch.object(sources, 'metadata', side_effect=[urls, DSC]), self.assertRaises(ValueError):
                sources.collect_source({'name': 'example', 'version': '1.0'}, Path(temp), Path(temp), {'remaining': 1000})

    def test_successful_collection_is_still_private_unapproved_and_layer_coverage_pending(self):
        inputs = {'unresolved': [], 'sources': [{'key': 'a' * 64, 'name': 'example', 'version': '1.0'}]}
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(sources, 'ROOT', Path(temp)), patch.object(sources, 'inventory', return_value=inputs), \
                 patch.object(sources, 'collect_source', return_value={'status': 'collected-unreviewed'}):
                path = sources.collect(Path('/unused'), 'runtime', 1000)
            data = json.loads((path / 'sources.json').read_text())
            self.assertTrue(data['collection_complete'])
            self.assertFalse(data['publication_approved'])
            self.assertTrue(any('all-layer' in item for item in data['pending']))
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path / 'sources.json').stat().st_mode & 0o777, 0o600)

    def test_collection_failure_is_recorded_and_not_waived(self):
        inputs = {'unresolved': [], 'sources': [{'key': 'a' * 64, 'name': 'example', 'version': '1.0'}]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(sources, 'ROOT', root), patch.object(sources, 'inventory', return_value=inputs), \
                 patch.object(sources, 'collect_source', side_effect=ValueError('missing exact version')), self.assertRaises(ValueError):
                sources.collect(Path('/unused'), 'runtime', 1000)
            path, = root.glob('.lab/distro-sources/*/sources.json')
            data = json.loads(path.read_text())
            self.assertTrue(data['complete'])
            self.assertFalse(data['collection_complete'])
            self.assertFalse(data['publication_approved'])
            self.assertEqual(data['sources'][0]['status'], 'collection-failed')


if __name__ == '__main__':
    unittest.main()
