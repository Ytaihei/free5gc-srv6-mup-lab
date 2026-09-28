"""Offline checks for private, immutable-image evidence and fail-closed review."""
import json
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_audit as audit

ID = 'sha256:' + 'a' * 64
OTHER = 'sha256:' + 'b' * 64


def inventory():
    return {'containers': [{'service': name, 'container_id': f'{i:064x}', 'image_id': ID,
                            'running': True} for i, name in enumerate(sorted(audit.SERVICES), 1)],
            'candidate': {'image_id': ID}, 'builder': None,
            'images': [{'Id': ID, 'Size': 123, 'Os': 'linux', 'Architecture': 'amd64'}]}


class ImageAuditTests(unittest.TestCase):
    def archive(self, path, oci=True, corrupt=False, extra=None, multi=False, ambiguous=False):
        layer = b'layer bytes for identity test'
        sha = lambda data: 'sha256:' + hashlib.sha256(data).hexdigest()
        encoded = lambda value: json.dumps(value).encode()
        config = encoded({'os': 'linux', 'architecture': 'amd64', 'rootfs': {'diff_ids': [sha(layer)]}})
        config_id = sha(config)
        if oci:
            name = lambda data: 'blobs/sha256/' + sha(data).split(':')[1]
            config_name, layer_name = name(config), name(layer)
            manifest = encoded({'schemaVersion': 2, 'config': {'digest': config_id, 'size': len(config)},
                                'layers': [{'digest': sha(layer), 'size': len(layer)}]})
            identifier = sha(manifest)
            files = {name(manifest): manifest,
                     'index.json': encoded({'schemaVersion': 2, 'manifests': [{'digest': identifier, 'size': len(manifest)}]})}
            if multi:
                entry = {'digest': identifier, 'size': len(manifest), 'platform': {'os': 'linux', 'architecture': 'amd64'}}
                other = entry if ambiguous else {'digest': OTHER, 'size': 1, 'platform': {'os': 'linux', 'architecture': 'arm64'}}
                index = encoded({'schemaVersion': 2, 'manifests': [entry, other]})
                identifier = sha(index)
                files[name(index)] = index
                files['index.json'] = encoded({'schemaVersion': 2, 'manifests': [{'digest': identifier, 'size': len(index)}]})
        else:
            identifier = config_id
            config_name, layer_name = config_id.split(':')[1] + '.json', 'layer/layer.tar'
            files = {}
        files.update({config_name: config, layer_name: b'corrupt' if corrupt else layer,
                      'manifest.json': encoded([{'Config': config_name, 'Layers': [layer_name]}])})
        with tarfile.open(path, 'w') as archive:
            for name, value in [*files.items(), *(extra or [])]:
                member = tarfile.TarInfo(name); member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
        return identifier, config_id

    def report(self):
        return {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Metadata': {'ImageID': ID},
                'Results': [{'Target': 'example', 'Packages': [{'Name': 'example', 'Version': '1', 'Licenses': ['MIT']}]}]}

    def test_inventory_requires_every_service_and_immutable_image_ids(self):
        data = inventory()
        self.assertEqual(set(audit.validate_inventory(data)[ID]), audit.SERVICES | {'bootstrap'})
        for change in ('missing', 'duplicate', 'stopped', 'image', 'tag', 'platform'):
            data = inventory()
            if change == 'missing': data['containers'].pop()
            if change == 'duplicate': data['containers'].append(data['containers'][0])
            if change == 'stopped': data['containers'][0]['running'] = False
            if change == 'image': data['images'] = []
            if change == 'tag': data['containers'][0]['image_id'] = 'example:latest'
            if change == 'platform': data['images'][0]['Architecture'] = 'arm64'
            with self.subTest(change=change), self.assertRaises(ValueError):
                audit.validate_inventory(data)

    def test_candidate_and_builder_are_scanned_even_if_not_running(self):
        data = inventory()
        data['builder'] = {'image_id': OTHER}
        data['images'].append(dict(data['images'][0], Id=OTHER))
        self.assertEqual(audit.validate_inventory(data)[OTHER], ['builder'])

    def test_selection_detects_container_or_builder_replacement(self):
        before, after = inventory(), inventory()
        self.assertEqual(audit.selection(before), audit.selection(after))
        after['containers'][0]['container_id'] = 'f' * 64
        self.assertNotEqual(audit.selection(before), audit.selection(after))
        after = inventory(); after['builder'] = {'image_id': OTHER}
        self.assertNotEqual(audit.selection(before), audit.selection(after))

    def test_overridden_bootstrap_nfs_are_still_scanned_and_bound_to_selection(self):
        before = inventory()
        before['candidate'].update(image_set_schema=1,
            nf_images={name: ID for name in audit.CORE_SERVICES if name != 'db'})
        after = json.loads(json.dumps(before))
        after['candidate']['nf_images']['free5gc-smf'] = OTHER
        after['images'].append(dict(after['images'][0], Id=OTHER))
        self.assertEqual(audit.validate_inventory(after)[OTHER], ['bootstrap:free5gc-smf'])
        self.assertNotEqual(audit.selection(before), audit.selection(after))
        after['candidate']['nf_images'].pop('free5gc-smf')
        with self.assertRaises(ValueError):
            audit.validate_inventory(after)

    def test_wrong_image_and_empty_inventory_fail_policy(self):
        self.assertEqual(audit.image_policy(self.report(), ID)['failures'], [])
        self.assertTrue(audit.image_policy(self.report(), OTHER)['failures'])
        self.assertTrue(audit.image_policy({}, ID)['failures'])

    def test_high_unfixed_and_secret_findings_are_not_suppressed(self):
        data = self.report()
        data['Results'][0]['Vulnerabilities'] = [{'Severity': 'HIGH', 'VulnerabilityID': 'CVE-test',
                                                'PkgName': 'example', 'InstalledVersion': '1'}]
        data['Results'][0]['Secrets'] = [{'Match': 'do-not-print-sensitive-value'}]
        result = audit.image_policy(data, ID)
        self.assertEqual(result['vulnerabilities_by_severity'], {'HIGH': 1})
        self.assertEqual(result['secret_findings'], 1)
        self.assertEqual(len(result['failures']), 2)
        self.assertNotIn('do-not-print-sensitive-value', json.dumps(result))

    def test_source_only_host_exception_does_not_approve_image_license(self):
        data = self.report()
        data['Results'][0]['Packages'] = [{'Name': 'ansible-core', 'Version': '2.21.3',
                                         'Licenses': ['GPL-3.0-or-later']}]
        review = audit.image_policy(data, ID)['license_review']
        self.assertFalse(review['distribution_approved'])
        self.assertEqual(review['packages'][0]['groups'], ['reciprocal'])
        self.assertIn('corresponding-source', review['packages'][0]['requires'])

    def test_scan_uses_archive_not_daemon_and_does_not_hide_findings(self):
        args = audit.image_scan_args(Path('/private/image.tar'), Path('/private/scan.json'))
        self.assertIn('--input', args)
        self.assertEqual(args[args.index('--scanners') + 1], 'vuln,license,secret')
        for option in ('--offline-scan', '--skip-db-update', '--skip-java-db-update', '--license-full', '--ignore-unfixed=false'):
            self.assertIn(option, args)
        self.assertNotIn('--server', args)
        self.assertEqual(args[args.index('--ignorefile') + 1], '/dev/null')

    def test_environment_cannot_silently_disable_scanning(self):
        with patch.dict(os.environ, {'TRIVY_SKIP_FILES': '*', 'TRIVY_SERVER': 'https://example.invalid'}):
            env = audit.scanner_environment(Path('/private/cache'))
        self.assertNotIn('TRIVY_SKIP_FILES', env)
        self.assertNotIn('TRIVY_SERVER', env)
        self.assertEqual(env['TRIVY_DISABLE_TELEMETRY'], 'true')

    def test_preview_never_saves_images_or_runs_scanner(self):
        with patch.object(audit, 'inventory', return_value=inventory()), \
             patch.object(audit.subprocess, 'run') as run, patch.object(audit.tempfile, 'mkdtemp') as mkdir:
            audit.audit(object())
        run.assert_not_called(); mkdir.assert_not_called()

    def test_scanner_failure_retains_private_incomplete_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            vm = type('VM', (), {'state': Path(temp)})()
            with patch.object(audit, 'inventory', return_value=inventory()), \
                 patch.object(audit.subprocess, 'run', side_effect=OSError('test scanner error')), \
                 self.assertRaisesRegex(ValueError, 'incomplete'):
                audit.audit(vm, scan=True)
            directories = list((Path(temp) / 'image-audits').iterdir())
            self.assertEqual(len(directories), 1)
            manifest = json.loads((directories[0] / 'manifest.json').read_text())
            self.assertFalse(manifest['complete'])
            self.assertFalse(manifest['publication_approved'])
            self.assertEqual(directories[0].stat().st_mode & 0o777, 0o700)
            self.assertEqual((directories[0] / 'manifest.json').stat().st_mode & 0o777, 0o600)

    def test_remote_inventory_never_executes_or_commits_running_containers(self):
        for operation in ("'exec'", "'commit'", "'export'", "'pull'", "'push'", "'start'", "'stop'"):
            self.assertNotIn(operation, audit.SNAPSHOT)
        self.assertIn("'container', 'inspect'", audit.SNAPSHOT)

    def test_source_only_scan_excludes_compact_state_and_developer_trees(self):
        script = (ROOT / 'scripts/supply-chain.sh').read_text()
        self.assertIn('--skip-dirs .git,.lab,worktrees,.cache,', script)
        self.assertIn('*.local.yaml', script)

    def test_oci_manifest_id_is_bound_to_verified_config_and_layers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            identifier, config_id = self.archive(path)
            self.assertNotEqual(identifier, config_id)
            proof = audit.verify_archive(path, identifier)
            self.assertEqual(proof['config_id'], config_id)
            self.assertEqual(proof['selected_image_id'], identifier)
            self.assertEqual(proof['format'], 'oci-manifest')

    def test_classic_docker_config_id_and_layers_are_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            identifier, config_id = self.archive(path, oci=False)
            proof = audit.verify_archive(path, identifier)
            self.assertEqual(proof['config_id'], config_id)
            self.assertEqual(proof['format'], 'docker-config')

    def test_archive_wrong_id_or_corrupt_layer_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            for oci in (True, False):
                identifier, _ = self.archive(path, oci=oci)
                with self.assertRaises(ValueError):
                    audit.verify_archive(path, OTHER)
                identifier, _ = self.archive(path, oci=oci, corrupt=True)
                with self.assertRaises(ValueError):
                    audit.verify_archive(path, identifier)

    def test_duplicate_or_traversal_archive_members_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            for name in ('manifest.json', '../outside', '/outside'):
                identifier, _ = self.archive(path, extra=[(name, b'{}')])
                with self.assertRaises(ValueError):
                    audit.verify_archive(path, identifier)

    def test_scanner_diff_ids_must_match_verified_archive(self):
        report = self.report()
        report['Metadata']['DiffIDs'] = [OTHER]
        self.assertTrue(audit.image_policy(report, ID, [ID])['failures'])
        self.assertEqual(audit.image_policy(report, ID, [OTHER])['failures'], [])

    def test_multiarch_index_binds_only_selected_linux_amd64_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            identifier, config_id = self.archive(path, multi=True)
            proof = audit.verify_archive(path, identifier)
            self.assertEqual(proof['format'], 'oci-index')
            self.assertEqual(proof['config_id'], config_id)
            self.assertNotEqual(proof['platform_manifest_id'], identifier)

    def test_multiarch_index_refuses_ambiguous_platform(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            identifier, _ = self.archive(path, multi=True, ambiguous=True)
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                audit.verify_archive(path, identifier)


if __name__ == '__main__':
    unittest.main()
