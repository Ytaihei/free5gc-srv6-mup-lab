"""All-layer dpkg inventory preserves replaced versions without extraction."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import image_source_layers as layers
import image_sources as sources
import test_image_sources


def status(version='2.0', extra=''):
    return (f'Package: example-bin\nVersion: {version}\nArchitecture: amd64\n'
            f'Status: install ok installed\n{extra}').encode()


def archive(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as result:
        for name, payload in members:
            member = tarfile.TarInfo(name)
            if payload is None:
                member.type = tarfile.SYMTYPE; member.linkname = '/outside'
                result.addfile(member)
            else:
                member.size = len(payload); result.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


class LayerSourceTests(unittest.TestCase):
    def test_source_omission_has_debian_defined_semantics_and_independent_epoch(self):
        item, = layers.packages(status())
        self.assertEqual((item['source'], item['source_version']), ('example-bin', '2.0'))
        item, = layers.packages(status('4:2.0', 'Source: example (1:1.0-1ubuntu1)\n'))
        self.assertEqual(item['source_version'], '1:1.0-1ubuntu1')
        item, = layers.packages(status('4:2.0', 'Source: example\n'))
        self.assertEqual(item['source_version'], '4:2.0')

    def test_embedded_sources_require_exact_versions(self):
        items = layers.packages(status(extra='Built-Using: embed (= 1.0),\n other (= 2:2.0)\nStatic-Built-Using: static (= 3.0)\n'))
        self.assertEqual([(x['source'], x['source_version']) for x in items[1:]],
                         [('embed', '1.0'), ('other', '2:2.0'), ('static', '3.0')])
        for value in ['embed', 'embed (>= 1.0)', 'embed (= 1.0) | other (= 2.0)']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                layers.packages(status(extra='Built-Using: ' + value + '\n'))

    def test_invalid_fields_or_versions_are_not_guessed(self):
        for data in [status(extra='version: 3.0\n'), b' continuation\n', status(extra='Source: bad/name\n'),
                     status().replace(b'Version: 2.0', b'Version: ../bad'), b'bad-field\n']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                layers.packages(data)
        self.assertEqual(layers.packages(b'Package: missing\nStatus: purge ok not-installed\n'), [])

    def test_all_recorded_versions_survive_whiteouts_and_status_old(self):
        raw = archive([('var/lib/dpkg/status', status()), ('var/lib/dpkg/status-old', status('1.0')),
                       ('var/lib/dpkg/.wh.status-old', b''), ('var/lib/dpkg/status.d/extra', status('0.9'))])
        result = layers.inspect_layer(io.BytesIO(raw))
        self.assertEqual([x['packages'][0]['source_version'] for x in result['metadata']], ['2.0', '1.0', '0.9'])
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(result['metadata'][0]['sha256'], hashlib.sha256(status()).hexdigest())

    def test_links_are_not_followed_and_oversized_metadata_is_rejected(self):
        result = layers.inspect_layer(io.BytesIO(archive([('var/lib/dpkg/status', None)])))
        self.assertEqual(result['metadata'], [])
        self.assertEqual(len(result['unresolved']), 1)
        with patch.object(layers, 'MAX_STATUS', 4), self.assertRaises(ValueError):
            layers.inspect_layer(io.BytesIO(archive([('var/lib/dpkg/status', status())])))

    def test_paths_cannot_traverse_or_be_absolute(self):
        for path in ['/var/lib/dpkg/status', '../var/lib/dpkg/status', 'var/../lib/dpkg/status', 'var//lib/dpkg/status']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                layers.inspect_layer(io.BytesIO(archive([(path, status())])))
        self.assertEqual(layers.safe_name('./var/lib/dpkg/status'), 'var/lib/dpkg/status')

    def test_layer_cache_uses_verified_blob_not_claimed_diff_id(self):
        a = archive([('var/lib/dpkg/status', status('1.0'))])
        b = archive([('var/lib/dpkg/status', status('2.0'))])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.tar'
            path.write_bytes(archive([('manifest.json', json.dumps([{'Layers': ['a', 'b', 'a']}]).encode()),
                                      ('a', a), ('b', b)]))
            proof = {'layer_digests': ['sha256:' + hashlib.sha256(x).hexdigest() for x in [a, b, a]],
                     'diff_ids': ['sha256:' + 'a' * 64] * 3}
            with patch.object(layers, 'inspect_layer', wraps=layers.inspect_layer) as inspect:
                result = layers.image_layers(path, proof, {})
            self.assertEqual(inspect.call_count, 2)
            self.assertEqual([x['metadata'][0]['packages'][0]['version'] for x in result], ['1.0', '2.0', '1.0'])

    def test_inventory_reports_missing_final_identity_instead_of_claiming_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identifier, _ = test_image_sources.ImageSourceTests().audit_fixture(root)
            with patch.object(sources, 'verify_archive', return_value={'config_id': identifier, 'diff_ids': []}), \
                 patch.object(layers, 'image_layers', return_value=[]):
                result = sources.inventory(root, 'all', True)
            self.assertEqual(result['os_inventory'], 'all-layer-dpkg-metadata')
            self.assertEqual(len(result['unresolved']), 1)
            self.assertIn('absent', result['unresolved'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
