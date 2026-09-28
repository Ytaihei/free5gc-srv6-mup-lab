"""Reviewed capture exceptions do not admit arbitrary binary artifacts."""
import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('capture_export', ROOT / 'scripts/export-source.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)
SAMPLE = 'examples/pcap/fixture/sample.pcap'
DOCS = {'examples/pcap/fixture/README.md', 'examples/pcap/fixture/README.ja.md'}


def pcap(payload=b'fixture packet'):
    frame = b'\x02' + b'\x00' * 13 + payload
    return (struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
            + struct.pack('<IIII', 1, 0, len(frame), len(frame)) + frame)


def entry(data, path=SAMPLE):
    return {'path': path, 'sha256': hashlib.sha256(data).hexdigest(),
            'bytes': len(data), 'packets': 1}


class ReviewedCaptureTests(unittest.TestCase):
    def test_reviewed_classic_capture_passes(self):
        data = pcap()
        EXPORT.check_capture(data, entry(data), ['fixture-private'])
        EXPORT.check_payload(SAMPLE, data, {SAMPLE: entry(data)}, [])

    def test_unreviewed_binary_is_not_a_source_member(self):
        for path in (SAMPLE, 'random.pcap', 'example.pcapng', 'example.bin'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                EXPORT.check_payload(path, pcap(), {}, [])

    def test_modified_bytes_size_and_count_are_rejected(self):
        data = pcap()
        for changed in (data[:-1] + b'!', data + b'\x00', data[:-1]):
            with self.subTest(data=changed), self.assertRaisesRegex(ValueError, 'digest/size'):
                EXPORT.check_capture(changed, entry(data))
        with self.assertRaisesRegex(ValueError, 'packet count'):
            EXPORT.check_capture(data, {**entry(data), 'packets': 2})

    def test_hash_cannot_approve_invalid_pcap_structure(self):
        data = pcap()
        variants = [b'\x0a\x0d\x0d\x0a' + data[4:],  # PCAPNG not supported
                    data[:20] + struct.pack('<I', 101) + data[24:],  # wrong link type
                    data[:28] + struct.pack('<I', 1_000_000) + data[32:],
                    data[:36] + struct.pack('<I', 9999) + data[40:],  # truncation
                    data + b'\x00', data[:15]]
        for changed in variants:
            with self.subTest(data=changed), self.assertRaises(ValueError):
                EXPORT.check_capture(changed, entry(changed))

    def test_binary_identity_check_covers_text_and_packed_ip(self):
        for marker, payload in [('fixture-private', b'FIXTURE-PRIVATE'),
                                ('203.0.113.41', ipaddress.ip_address('203.0.113.41').packed),
                                ('2001:db8::1234', ipaddress.ip_address('2001:db8::1234').packed)]:
            data = pcap(payload)
            with self.subTest(marker=marker), self.assertRaises(ValueError) as error:
                EXPORT.check_capture(data, entry(data), [marker])
            self.assertNotIn(marker, str(error.exception))

    def test_manifest_requires_exact_paths_bounds_and_documentation(self):
        good = entry(pcap())
        names = {SAMPLE, EXPORT.CAPTURE_POLICY} | DOCS
        for captures, selected in [
                ([good, good], names),
                ([{**good, 'path': 'artifacts/raw.pcap'}], names),
                ([{**good, 'bytes': 1_048_577}], names),
                ([{**good, 'packets': True}], names),
                ([{**good, 'sha256': 'not a hash'}], names),
                ([good], names - {'examples/pcap/fixture/README.ja.md'}),
                ([], names),
                ([good], names | {'examples/pcap/fixture/unreviewed.pcap'})]:
            with self.subTest(captures=captures, names=selected), self.assertRaises(ValueError):
                EXPORT.capture_inventory(selected, lambda _: json.dumps({'version': 1, 'captures': captures}))

    def test_real_sample_digests_and_exact_ignore_exceptions(self):
        policy = json.loads((ROOT / EXPORT.POLICY).read_text())
        captures = EXPORT.capture_inventory(policy['files'], lambda name: (ROOT / name).read_bytes())
        self.assertEqual(len(captures), 7)
        self.assertEqual(sum(item['bytes'] for item in captures.values()), 64452)
        for name, record in captures.items():
            EXPORT.check_capture((ROOT / name).read_bytes(), record)
        exceptions = {line[2:] for line in (ROOT / '.gitignore').read_text().splitlines()
                      if line.startswith('!/examples/pcap/')}
        self.assertEqual(exceptions, set(captures))


class CaptureExportIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        for key, value in [('user.name', 'Example Author'), ('user.email', 'author@example.invalid'),
                           ('commit.gpgsign', 'false'), ('core.hooksPath', '/dev/null')]:
            self.git('config', key, value)
        self.names = EXPORT.REQUIRED | DOCS | {SAMPLE, EXPORT.CAPTURE_POLICY}
        for name in self.names:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture\n')
        self.data = pcap()
        (self.repo / SAMPLE).write_bytes(self.data)
        self.write_manifest(entry(self.data))
        self.write_inventory()
        self.commit()

    def git(self, *args):
        return EXPORT.git(self.repo, *args)

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'reviewed fixture')

    def write_manifest(self, record):
        (self.repo / EXPORT.CAPTURE_POLICY).write_text(json.dumps({'version': 1, 'captures': [record]}))

    def write_inventory(self):
        (self.repo / EXPORT.POLICY).write_text(json.dumps({
            'version': 1, 'files': sorted(self.names), 'private_only': []}))

    def test_tree_export_and_history_accept_identical_reviewed_bytes(self):
        EXPORT.check_tree(self.repo)
        output = self.root / 'source.tar.gz'
        result = EXPORT.export(self.repo, 'HEAD', output)
        self.assertFalse(result['publication_approved'])
        with tarfile.open(output) as archive:
            self.assertEqual(archive.extractfile('free5gc-srv6-mup-lab/' + SAMPLE).read(), self.data)
        self.assertTrue(EXPORT.check_history(self.repo)['history_matches_public_source_policy'])

    def test_export_loads_review_from_commit_not_worktree(self):
        self.write_manifest({**entry(self.data), 'sha256': '0' * 64})
        EXPORT.export(self.repo, 'HEAD', self.root / 'source.tar.gz')
        with self.assertRaisesRegex(ValueError, 'digest/size'):
            EXPORT.check_tree(self.repo)

    def test_changed_review_is_rechecked_for_unchanged_historical_blob(self):
        self.write_manifest({**entry(self.data), 'sha256': '0' * 64})
        self.commit()
        result = EXPORT.check_history(self.repo)
        self.assertIn('forbidden_or_private_source_version', result['issues'])

    def test_unknown_capture_cannot_be_admitted_by_source_allowlist(self):
        unknown = 'examples/pcap/fixture/unreviewed.pcap'
        (self.repo / unknown).write_bytes(self.data)
        self.names.add(unknown)
        self.write_inventory()
        self.commit()
        with self.assertRaises(ValueError):
            EXPORT.export(self.repo, 'HEAD', self.root / 'refused.tar.gz')
        self.assertFalse((self.root / 'refused.tar.gz').exists())

    def test_binary_identity_failure_never_writes_archive(self):
        output = self.root / 'refused.tar.gz'
        with self.assertRaisesRegex(ValueError, 'matched value redacted'):
            EXPORT.export(self.repo, 'HEAD', output, ['fixture packet'])
        self.assertFalse(output.exists())
