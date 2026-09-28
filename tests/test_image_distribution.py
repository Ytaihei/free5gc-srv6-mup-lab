"""Artifact-bound material collection and license/key classification tests."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from compact_config import module
import image_distribution as review
import compact_audit as audit
materials = module('image_materials', ROOT / 'scripts/image-materials.py')


class ImageDistributionTests(unittest.TestCase):
    def policy(self):
        return yaml.safe_load((ROOT / 'config/image-distribution-policy.yml').read_text())

    def report(self, label):
        return {'SchemaVersion': 2, 'Results': [{'Packages': [{'Name': 'sample', 'Version': '1', 'Licenses': [label]}]}]}

    def test_known_copyleft_produces_requirements_not_a_blanket_ban_or_approval(self):
        for name, requirement in [('GPL-2.0-only', 'corresponding-source'),
                                  ('LGPL-2.1-only', 'linking-and-relinking-review'),
                                  ('AGPL-3.0-only', 'network-source-access-review'),
                                  ('SSPL-1.0', 'service-scope-review'), ('MIT', 'copyright-and-notices')]:
            result = review.classify(self.report(name), self.policy())
            self.assertFalse(result['distribution_approved'])
            self.assertIn(requirement, result['packages'][0]['requires'])
            self.assertEqual(result['packages'][0]['materials_status'], 'pending-review')

    def test_ambiguous_unknown_and_compound_labels_still_need_review(self):
        for label in ['public-domain', 'GPL-2.0', 'MIT OR CustomLicense', 'unrecognized']:
            result = review.classify(self.report(label), self.policy())
            self.assertEqual(result['packages'][0]['groups'], ['unreviewed'])
            self.assertTrue(audit.image_policy(self.report(label), 'sha256:' + 'a' * 64)['failures'])

    def test_missing_metadata_is_not_invented(self):
        data = self.report('MIT'); data['Results'][0]['Packages'][0]['Licenses'] = []
        result = review.classify(data, self.policy())
        self.assertEqual(result['packages'][0]['groups'], ['unknown'])

    def test_full_license_file_headers_without_packages_keep_obligations(self):
        data = self.report('MIT')
        data['Results'].append({'Target': 'Loose File License(s)', 'Licenses': [
            {'Name': 'GPL-2.0-only', 'FilePath': 'embedded/source.h', 'PkgName': ''}]})
        result = review.classify(data, self.policy())
        self.assertEqual(result['file_licenses'][0]['file_path'], 'embedded/source.h')
        self.assertIn('corresponding-source', result['file_licenses'][0]['requires'])

    def test_license_groups_cannot_silently_shadow_each_other(self):
        policy = self.policy()
        policy['license_groups']['reciprocal']['licenses'].append('MIT')
        with self.assertRaises(ValueError):
            review.license_index(policy)

    def test_key_provenance_needs_exact_path_and_full_file_hash(self):
        policy = json.loads((ROOT / 'config/public-test-keys.json').read_text())
        item = policy['keys'][0]
        result = review.key_origin(item['path'], {item['sha256']}, policy)
        self.assertEqual(result['classification'], 'public-upstream-default-key')
        self.assertFalse(result['scanner_exempted'])
        for path, hashes in [('/different/path', {item['sha256']}), (item['path'], set()),
                             (item['path'], {'0' * 64}), (item['path'], {item['sha256'], '0' * 64})]:
            self.assertEqual(review.key_origin(path, hashes, policy)['classification'], 'unverified')

    def make_archive(self, path, entries):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as layer:
            for name, value, link in entries:
                item = tarfile.TarInfo(name)
                if link:
                    item.type = tarfile.SYMTYPE; item.linkname = link
                    layer.addfile(item)
                else:
                    item.size = len(value); layer.addfile(item, io.BytesIO(value))
        with tarfile.open(path, 'w') as archive:
            for name, value in [('manifest.json', b'[{"Layers":["layer.tar"]}]'), ('layer.tar', data.getvalue())]:
                member = tarfile.TarInfo(name); member.size = len(value)
                archive.addfile(member, io.BytesIO(value))

    def test_layer_reader_keeps_keys_out_of_notice_blobs_and_never_follows_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); blobs = root / 'blobs'; blobs.mkdir()
            self.make_archive(root / 'image.tar', [
                ('free5gc/cert/nrf.key', b'private-key-test-content', None),
                ('usr/share/doc/a/copyright', b'copyright text', None),
                ('usr/share/doc/b/copyright', b'', '/etc/passwd'),
                ('unsafe/LICENSE', b'-----BEGIN ' + b'PRIVATE KEY-----', None)])
            notices, hashes = materials.layer_materials(root / 'image.tar', {'/free5gc/cert/nrf.key'}, blobs)
            self.assertEqual(hashes['/free5gc/cert/nrf.key'], {hashlib.sha256(b'private-key-test-content').hexdigest()})
            self.assertEqual([p.read_bytes() for p in blobs.iterdir()], [b'copyright text'])
            self.assertEqual({n['status'] for n in notices}, {'collected-unreviewed', 'nonregular-needs-review', 'sensitive-pattern-not-copied'})

    def test_layer_traversal_is_rejected_not_extracted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'blobs').mkdir()
            self.make_archive(root / 'image.tar', [('../LICENSE', b'bad', None)])
            with self.assertRaises(ValueError):
                materials.layer_materials(root / 'image.tar', set(), root / 'blobs')
        for name in ['/LICENSE', 'a/../LICENSE', '././LICENSE', 'a//LICENSE']:
            with self.assertRaises(ValueError):
                materials.name_in_layer(name)

    def test_module_download_does_not_inherit_private_proxy_or_execute_builds(self):
        with patch.dict('os.environ', {'GOPRIVATE': '*', 'GOSUMDB': 'off', 'GOFLAGS': '-modfile=private'}), \
             patch.object(materials.subprocess, 'check_output', return_value=b'{"Error":"missing"}') as download:
            with self.assertRaises(ValueError):
                materials.go_module_materials(Path('/go'), 'example.com/a', 'v1.2.3', Path('/private'), Path('/blobs'))
        command = download.call_args.args[0]
        self.assertEqual(command, ['/go', 'mod', 'download', '-json', 'example.com/a@v1.2.3'])
        env = download.call_args.kwargs['env']
        self.assertEqual(env['GOPRIVATE'], '')
        self.assertEqual(env['GOSUMDB'], 'sum.golang.org')
        self.assertEqual(env['GOWORK'], 'off')
        self.assertEqual(env['GOFLAGS'], '')

    def test_materials_refuse_incomplete_audit_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'manifest.json').write_text('{}')
            with patch.object(materials.tempfile, 'mkdtemp') as mkdir, self.assertRaises(ValueError):
                materials.prepare(root)
            mkdir.assert_not_called()

    def test_gosu_source_mapping_retains_original_artifact_version(self):
        mapping = self.policy()['go_source_mappings'][0]
        self.assertEqual(mapping['scanner_version'], 'v1.19.0')
        self.assertEqual(mapping['name'], 'github.com/tianon/gosu')
        self.assertTrue(mapping['module_version'].endswith(mapping['commit'][:12]))
        self.assertEqual(mapping['evidence'], 'https://github.com/tianon/gosu/releases/tag/1.19')


if __name__ == '__main__':
    unittest.main()
