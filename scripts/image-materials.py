#!/usr/bin/env python3
"""Prepare PRIVATE review materials from an immutable image audit, never publish."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

import yaml

from compact_config import ROOT, locks
from compact_vm import digest, write
from compact_audit import IMAGE_ID, image_policy, verify_archive
from image_distribution import key_origin

MAX_TEXT = 2 * 1024**2


def name_in_layer(value):
    if value.startswith('./'):
        value = value[2:]
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value.rstrip('/'):
        raise ValueError('unsafe layer path')
    return str(path)


def is_notice(name):
    path = PurePosixPath(name)
    return (bool(re.fullmatch(r'(LICENSE|NOTICE|COPYING|COPYRIGHT)([._-].*)?', path.name, re.I))
            or bool(re.fullmatch(r'usr/share/doc/[^/]+/copyright', name))
            or name.startswith('usr/share/common-licenses/'))


def put_blob(directory, data):
    sha = hashlib.sha256(data).hexdigest()
    path = directory / sha
    if not path.exists():
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(0o600)
    elif digest(path) != sha:
        raise ValueError('existing content blob mismatch')
    return sha


def layer_materials(archive_path, targets, blobs):
    """Read bounded selected files without extracting paths or following links.

    Records cover ALL layer copies, not a reconstructed final filesystem.
    Whiteouts and links are retained as metadata; never interpreted as permission
    to omit older bytes from redistribution review.
    """
    notices, hashes = [], {path: set() for path in targets}
    with tarfile.open(archive_path) as archive:
        layers = json.load(archive.extractfile('manifest.json'))[0]['Layers']
        for number, layer_name in enumerate(layers):
            with tarfile.open(fileobj=archive.extractfile(layer_name), mode='r|*') as layer:
                for member in layer:
                    name = name_in_layer(member.name)
                    absolute = '/' + name
                    if absolute not in targets and not is_notice(name):
                        continue
                    if not member.isfile():
                        if is_notice(name):
                            notices.append({'layer': number, 'path': name, 'status': 'nonregular-needs-review'})
                        continue
                    if member.size > MAX_TEXT:
                        raise ValueError('selected key/notice file exceeds review size limit')
                    data = layer.extractfile(member).read()
                    if absolute in targets:
                        hashes[absolute].add(hashlib.sha256(data).hexdigest())
                        # Never write or print key contents, even known public fixtures.
                        continue
                    if b'PRIVATE KEY-----' in data:
                        notices.append({'layer': number, 'path': name, 'status': 'sensitive-pattern-not-copied'})
                        continue
                    notices.append({'layer': number, 'path': name, 'sha256': put_blob(blobs, data),
                                    'status': 'collected-unreviewed'})
    return notices, hashes


def go_module_materials(go, name, version, directory, blobs):
    if not re.fullmatch(r'[a-zA-Z0-9._~/-]+', name) or not name or '..' in name.split('/'):
        raise ValueError('invalid Go module path')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?(?:\+incompatible)?', version or ''):
        raise ValueError('module version is not a fixed Go version')
    environment = dict(os.environ, GOPROXY='https://proxy.golang.org', GOSUMDB='sum.golang.org',
                       GOPRIVATE='', GONOPROXY='', GONOSUMDB='', GOINSECURE='',
                       GOTOOLCHAIN='local', GOENV='off', GOFLAGS='')
    # An empty private cwd prevents ambient go.mod/go.work/go.env from changing
    # resolution. Download only: no third-party build/generate/install is run.
    environment['GOWORK'] = 'off'
    raw = subprocess.check_output([str(go), 'mod', 'download', '-json', name + '@' + version],
                                  cwd=directory, env=environment, timeout=180, stderr=subprocess.DEVNULL)
    item = json.loads(raw)
    if (item.get('Error') or item.get('Path') != name or item.get('Version') != version
            or not item.get('Sum', '').startswith('h1:') or not item.get('GoModSum', '').startswith('h1:')):
        raise ValueError('module identity/checksum could not be verified')
    archive = Path(item['Zip'])
    if not archive.is_file() or archive.is_symlink() or archive.stat().st_size > 512 * 1024**2:
        raise ValueError('module source archive missing or too large')
    archive_sha = digest(archive)
    destination = directory / (archive_sha + '.zip')
    if not destination.exists():
        shutil.copyfile(archive, destination); destination.chmod(0o600)
    if digest(destination) != archive_sha:
        raise ValueError('copied module source digest mismatch')
    notices = []
    with zipfile.ZipFile(destination) as source:
        for entry in source.infolist():
            name_in_layer(entry.filename.rstrip('/'))
            if entry.is_dir() or not is_notice(entry.filename):
                continue
            if entry.file_size > MAX_TEXT:
                raise ValueError('module notice exceeds size limit')
            data = source.read(entry)
            if b'PRIVATE KEY-----' in data:
                raise ValueError('unexpected sensitive pattern in module notice')
            notices.append({'path': entry.filename, 'sha256': put_blob(blobs, data)})
    return {'name': name, 'version': version, 'sum': item['Sum'], 'go_mod_sum': item['GoModSum'],
            'archive_sha256': archive_sha, 'notices': notices,
            'status': 'collected-unreviewed' if notices else 'license-text-missing'}


def prepare(audit_directory, download=False, go=None):
    audit_directory = audit_directory.resolve()
    manifest_path = audit_directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get('complete') or not manifest.get('images'):
        raise ValueError('requires a complete immutable-image audit (policy may have failed)')
    policy = json.loads((ROOT / 'config/public-test-keys.json').read_text())
    parent = ROOT / '.lab/distribution-materials'
    if parent.is_symlink() or (ROOT / '.lab').is_symlink():
        raise ValueError('refusing symlink private state directory')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    blobs, sources = directory / 'notice-blobs', directory / 'go-sources'
    blobs.mkdir(mode=0o700); sources.mkdir(mode=0o700)
    review = {'schema_version': 1, 'complete': False, 'publication_approved': False,
              'audit_manifest_sha256': digest(manifest_path),
              'image_policy_sha256': digest(ROOT / 'config/image-distribution-policy.yml'),
              'key_provenance_sha256': digest(ROOT / 'config/public-test-keys.json'),
              'implementation_sha256': {name: digest(ROOT / 'scripts' / name) for name in
                                       ('image-materials.py', 'image_distribution.py', 'compact_audit.py', 'supply-chain-policy.py')},
              'images': [], 'go_modules': [],
              'scope': 'private materials collected for review; not a ready-to-distribute bundle'}
    modules = set()
    image_policy_config = yaml.safe_load((ROOT / 'config/image-distribution-policy.yml').read_text())
    source_mappings = {(x['name'], x['scanner_version']): x
                       for x in image_policy_config.get('go_source_mappings', [])}
    def save():
        write(directory / 'materials.json', json.dumps(review, indent=2) + '\n')
    save()
    try:
        seen = set()
        for item in manifest['images']:
            identifier = item['image_id']
            if not IMAGE_ID.fullmatch(identifier) or identifier in seen or not item.get('complete'):
                raise ValueError('invalid or duplicate audited image')
            seen.add(identifier)
            path = audit_directory / identifier.split(':')[1]
            for filename, field in [('image.tar', 'archive_sha256'), ('scan.json', 'report_sha256'), ('sbom.cdx.json', 'sbom_sha256')]:
                file = path / filename
                if file.is_symlink() or not file.is_file() or digest(file) != item[field]:
                    raise ValueError('audit artifact changed or is missing')
            proof = verify_archive(path / 'image.tar', identifier)
            report = json.loads((path / 'scan.json').read_text())
            targets = {r['Target'] for r in report['Results'] if r.get('Secrets')}
            notices, hashes = layer_materials(path / 'image.tar', targets, blobs)
            keys = [{'path': target, **key_origin(target, hashes[target], policy)} for target in sorted(targets)]
            result = image_policy(report, proof['config_id'], proof['diff_ids'])
            image_dir = directory / identifier.split(':')[1]; image_dir.mkdir(mode=0o700)
            write(image_dir / 'license-inventory.json', json.dumps(result['license_review'], indent=2) + '\n')
            write(image_dir / 'policy.json', json.dumps(result, indent=2) + '\n')
            shutil.copyfile(path / 'sbom.cdx.json', image_dir / 'sbom.cdx.json')
            (image_dir / 'sbom.cdx.json').chmod(0o600)
            write(image_dir / 'notices.json', json.dumps(notices, indent=2) + '\n')
            review['images'].append({'image_id': identifier, 'roles': item['roles'], 'keys': keys,
                                     'notices': len(notices), 'artifact_identity': proof,
                                     'policy_findings': len(result['failures'])})
            for r in report['Results']:
                if r.get('Type') == 'gobinary':
                    modules.update((p['Name'], p['Version']) for p in r.get('Packages') or []
                                   if p.get('Version') and p['Name'] != 'stdlib')
            print(f'Materials inventoried: {", ".join(item["roles"])}; key contents not copied', flush=True)
            save()
        if download:
            if go is None:
                raise ValueError('--go is required with --download-go-sources')
            actual = subprocess.check_output([str(go), 'version'], text=True).split()[2]
            if actual != 'go' + locks()['toolchains']['go']['version']:
                raise ValueError('use the repository-locked Go toolchain')
            for name, version in sorted(modules):
                try:
                    mapping = source_mappings.get((name, version))
                    source_version = mapping['module_version'] if mapping else version
                    record = go_module_materials(go, name, source_version, sources, blobs)
                    if mapping:
                        if not re.fullmatch(r'[a-f0-9]{40}', mapping['commit']) or not source_version.endswith(mapping['commit'][:12]):
                            raise ValueError('invalid reviewed source mapping')
                        record.update(scanner_version=version, source_mapping=mapping)
                except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
                    record = {'name': name, 'version': version, 'status': 'collection-failed', 'error': type(error).__name__}
                review['go_modules'].append(record)
                save()
        else:
            review['go_modules'] = [{'name': n, 'version': v, 'status': 'not-collected'} for n, v in sorted(modules)]
        review['complete'] = True
        review['collection_failures'] = sum(x['status'] == 'collection-failed' for x in review['go_modules'])
        review['pending'] = [
            'review exact-version license texts; resolve unknown/ambiguous labels and notice symlinks',
            'complete C/C++, eBPF, Go stdlib, frontend and distro corresponding-source coverage',
            'match modified main-program build snapshots, patches and effective module locks to binaries',
            'retain exact distro source packages plus build/install scripts, not just upstream links',
            'review AGPL network source access, LGPL linking and MongoDB SSPL service scope',
            'review all layers/history and source archives for private material before distribution',
            'resolve vulnerability/EOL findings and default-key runtime use; obtain human release approval']
    finally:
        save()
        print(f'Private distribution review: {directory}', flush=True)
    if review.get('collection_failures'):
        raise ValueError('some Go sources could not be collected; see private materials.json')
    return directory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit_directory', type=Path)
    parser.add_argument('--download-go-sources', action='store_true')
    parser.add_argument('--go', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    prepare(args.audit_directory, args.download_go_sources, args.go)
