#!/usr/bin/env python3
"""Private all-layer identity/key-pattern evidence, not publication approval."""
import argparse
import hashlib
import ipaddress
import json
import re
import tarfile
import tempfile
from pathlib import Path

from compact_config import ROOT, locks, module
from compact_vm import digest, write
from compact_audit import verify_archive

materials = module('layer_review_materials', ROOT / 'scripts/image-materials.py')
KEY = re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----')
PEM = re.compile(rb'-----BEGIN ((?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY)-----\s+'
                 rb'[A-Za-z0-9+/=\r\n]{32,16384}-----END \1-----')
CHUNK = 1024 * 1024


def scan_bytes(stream, markers, binary_markers=()):
    count, tail, secret, private, packed, pem = 0, b'', False, False, False, False
    sha = hashlib.sha256()
    overlap = max([32768, *(len(value) for value in markers)])
    while block := stream.read(CHUNK):
        sha.update(block); count += len(block)
        window = tail + block
        secret = secret or bool(KEY.search(window))
        pem = pem or bool(PEM.search(window))
        lowered = window.lower()
        private = private or any(value in lowered for value in markers)
        packed = packed or any(value in window for value in binary_markers)
        tail = window[-overlap:]
    return {'bytes': count, 'sha256': sha.hexdigest(), 'key_pattern': secret, 'complete_pem_pattern': pem,
            'private_identity': private or packed, 'text_identity': private, 'packed_address_candidate': packed}


def upstream_go(path):
    if path is None:
        return {}
    if path.is_symlink() or digest(path) != locks()['toolchains']['go']['sha256']:
        raise ValueError('upstream Go archive does not match the locked full checksum')
    known = {}
    with tarfile.open(path, 'r|*') as archive:
        for item in archive:
            if not item.isfile() or not item.name.startswith('go/'):
                continue
            name = materials.name_in_layer(item.name)
            result = scan_bytes(archive.extractfile(item), [])
            if result['key_pattern']:
                known['opt/' + name] = result['sha256']
    return known


def layer_review(stream, markers, known, binary_markers=()):
    result = {'files': 0, 'bytes': 0, 'identities': [], 'key_patterns': []}
    # Stream members without extracting names or following any links. Whiteouts
    # do not suppress the content of previous layers.
    with tarfile.open(fileobj=stream, mode='r|*') as layer:
        for item in layer:
            name = materials.name_in_layer(item.name)
            metadata = (name + '\n' + item.linkname).encode().lower()
            if any(value in metadata for value in markers):
                result['identities'].append({'path_sha256': hashlib.sha256(name.encode()).hexdigest(), 'where': 'metadata'})
            if not item.isfile():
                continue
            data = scan_bytes(layer.extractfile(item), markers, binary_markers)
            result['files'] += 1; result['bytes'] += data['bytes']
            if data['private_identity']:
                # Do not disclose a matched private path, identifier or value.
                result['identities'].append({'path_sha256': hashlib.sha256(name.encode()).hexdigest(), 'where': 'content',
                                            'text': data['text_identity'], 'packed_address': data['packed_address_candidate']})
            if data['key_pattern']:
                result['key_patterns'].append({'path': name, 'sha256': data['sha256'],
                    'complete_pem_pattern': data['complete_pem_pattern'],
                    'classification': 'locked-public-go-source' if known.get(name) == data['sha256'] else 'unverified'})
    return result


def review(directory, denylist, go_archive=None):
    values = json.loads(denylist.read_text())
    if not isinstance(values, list) or not values or not all(isinstance(x, str) and x.strip() for x in values):
        raise ValueError('private identity denylist must be a nonempty string array')
    markers = [x.lower().encode() for x in values]
    binary_markers = []
    # Packed addresses can have accidental matches in large binaries. Preserve
    # such candidates for review, never treat them as a confirmed disclosure.
    for value in values:
        try:
            binary_markers.append(ipaddress.ip_address(value).packed)
        except ValueError:
            pass
    manifest = json.loads((directory / 'manifest.json').read_text())
    if not manifest.get('complete'):
        raise ValueError('requires a complete immutable-image audit')
    known = upstream_go(go_archive)
    parent = ROOT / '.lab/image-layer-reviews'
    if parent.is_symlink():
        raise ValueError('refusing symlink review directory')
    parent.mkdir(mode=0o700, exist_ok=True, parents=True)
    destination = Path(tempfile.mkdtemp(prefix='review-', dir=parent))
    record = {'schema_version': 1, 'complete': False, 'publication_approved': False,
              'manifest_sha256': digest(directory / 'manifest.json'), 'denylist_sha256': digest(denylist),
              'implementation_sha256': digest(Path(__file__)), 'images': [], 'layers': {},
              'scope': 'all regular layer bytes and image config; finite identity list and PEM key patterns only'}
    try:
        for item in manifest['images']:
            path = directory / item['image_id'].split(':')[1] / 'image.tar'
            if digest(path) != item['archive_sha256']:
                raise ValueError('audited archive changed')
            proof = verify_archive(path, item['image_id'])
            with tarfile.open(path) as archive:
                saved = json.load(archive.extractfile('manifest.json'))[0]
                config = scan_bytes(archive.extractfile(saved['Config']), markers, binary_markers)
                for name, identity in zip(saved['Layers'], proof['diff_ids'], strict=True):
                    if identity not in record['layers']:
                        record['layers'][identity] = layer_review(archive.extractfile(name), markers, known, binary_markers)
                record['images'].append({'image_id': item['image_id'], 'roles': item['roles'],
                                         'diff_ids': proof['diff_ids'], 'config': config})
            print('Reviewed all saved layers: ' + ', '.join(item['roles']), flush=True)
        record['complete'] = True
        record['identity_candidates'] = sum(len(x['identities']) for x in record['layers'].values()) + sum(
            bool(x['config']['private_identity']) for x in record['images'])
        record['unverified_key_patterns'] = sum(x['classification'] == 'unverified'
            for layer in record['layers'].values() for x in layer['key_patterns']) + sum(
            bool(x['config']['key_pattern']) for x in record['images'])
    finally:
        write(destination / 'review.json', json.dumps(record, indent=2) + '\n')
        print('Private all-layer review: ' + str(destination), flush=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit_directory', type=Path)
    parser.add_argument('--denylist', type=Path, required=True)
    parser.add_argument('--upstream-go-archive', type=Path)
    args = parser.parse_args()
    review(args.audit_directory, args.denylist, args.upstream_go_archive)
