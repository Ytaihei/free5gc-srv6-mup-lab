"""All-layer checks retain deleted bytes and never expose matched values."""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from compact_config import module
review = module('image_layer_review', ROOT / 'scripts/image-layer-review.py')


class LayerTests(unittest.TestCase):
    def test_chunk_boundary_and_key_pattern_hash(self):
        key = b'-----BEGIN ' + b'PRIVATE KEY-----'
        data = b'x' * (review.CHUNK - 3) + b'private-marker' + key
        result = review.scan_bytes(io.BytesIO(data), [b'private-marker'])
        self.assertTrue(result['private_identity']); self.assertTrue(result['key_pattern'])
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertNotIn('private-marker', str(result))

    def test_whiteout_does_not_erase_earlier_matching_content(self):
        stream = io.BytesIO()
        key = b'-----BEGIN ' + b'PRIVATE KEY-----'
        with tarfile.open(fileobj=stream, mode='w') as tar:
            for name, data in [('opt/go/sample', key), ('opt/go/.wh.sample', b'')]:
                item = tarfile.TarInfo(name); item.size = len(data); tar.addfile(item, io.BytesIO(data))
            link = tarfile.TarInfo('link'); link.type = tarfile.SYMTYPE; link.linkname = '/private-marker'
            tar.addfile(link)
        stream.seek(0)
        result = review.layer_review(stream, [b'private-marker'], {'opt/go/sample': hashlib.sha256(key).hexdigest()})
        self.assertEqual(result['key_patterns'][0]['classification'], 'locked-public-go-source')
        self.assertEqual(result['files'], 2)
        self.assertEqual(len(result['identities']), 1)
        self.assertNotIn('private-marker', str(result))

    def test_upstream_fixture_requires_full_archive_checksum(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            review.upstream_go(Path('/missing-archive'))

    def test_packed_bytes_are_not_case_folded_and_delimiter_is_not_a_pem_block(self):
        result = review.scan_bytes(io.BytesIO(b'Axyz'), [], [b'axyz'])
        self.assertFalse(result['packed_address_candidate'])
        value = b'-----BEGIN ' + b'PRIVATE KEY-----'
        self.assertFalse(review.scan_bytes(io.BytesIO(value), [])['complete_pem_pattern'])
        pem = value + b'\n' + b'A' * 64 + b'\n-----END ' + b'PRIVATE KEY-----'
        self.assertTrue(review.scan_bytes(io.BytesIO(pem), [])['complete_pem_pattern'])


if __name__ == '__main__':
    unittest.main()
