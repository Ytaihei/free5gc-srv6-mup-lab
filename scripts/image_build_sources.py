#!/usr/bin/env python3
"""Collect compact build evidence privately from an explicitly owned VM.

Read-only guest access; no builds, activation, source execution or publication.
Recorded source-to-build relationships are not signed/reproducible-build proof.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from compact_config import ROOT, load
from compact_develop import NF_NAMES, OWN, safe_source_name
from compact_vm import VM, digest
from image_sources import atomic, inventory, SHA
from image_source_layers import safe_name

MAX_SOURCE = 512 * 1024**2
MAX_RECORD = 8 * 1024**2
# The pinned Material UI icon package has over 31,000 small members. Bound
# metadata separately from compressed (64 MiB) and expanded (512 MiB) bytes;
# these ZIPs are inventoried as data, never extracted or executed.
MAX_FRONTEND_ZIP_ENTRIES = 100000
BUILD_PATH = re.compile(r'bootstrap/[a-f0-9]{32}/nfs/[a-f0-9]{32}')
COMMON = tuple(OWN) + ('vinbero', 'ueransim')
COMMON_PATH = r'bootstrap/[a-f0-9]{32}/common/(?:' + '|'.join(COMMON) + ')'
# Each read validates canonical, regular, bounded files under the runtime root.
# Never copy arbitrary guest trees, writable container layers, volumes or keys.
READ = '''
import os, stat, sys
from pathlib import Path
root = Path('/opt/srv6-mup-compact/.lab/runtime')
path = root / sys.argv[1]
if root.resolve() != root or path.resolve() != path or not path.is_relative_to(root):
    raise ValueError('noncanonical build evidence path')
with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
    info = os.fstat(source.fileno())
    limit = int(sys.argv[2])
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError('build evidence type/size rejected')
    size = 0
    while block := source.read(min(1048576, limit - size + 1)):
        size += len(block)
        if size > limit:
            raise ValueError('growing build evidence rejected')
        sys.stdout.buffer.write(block)
    if size != info.st_size:
        raise ValueError('build evidence changed during read')
'''


def fetch(vm, relative, target, limit=MAX_RECORD):
    record_path = '(?:' + BUILD_PATH.pattern + '|' + COMMON_PATH + ')'
    if relative != 'candidate.json' and not re.fullmatch(
            record_path + r'/(?:build.json|payload.json|build.sh|source.tar|source-manifest.json|frontend-sources.tar|image/Dockerfile|provenance/go\.(?:mod|sum))'
            + r'|bootstrap/[a-f0-9]{32}/(?:image/Dockerfile|LICENSE|THIRD_PARTY_NOTICES.md)', relative):
        raise ValueError('unsupported guest evidence path')
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with target.open('xb') as stream:
        target.chmod(0o600)
        vm.ssh('sudo python3 -c ' + shlex.quote(READ) + ' ' + shlex.quote(relative) + ' ' + str(limit),
               stdout=stream, timeout=120)
    if target.stat().st_size > limit:
        raise ValueError('build evidence exceeds host size limit')


def source_proof(path, record):
    with tarfile.open(path / 'source.tar') as archive:
        members, total = {}, 0
        for item in archive:
            total += item.size
            if (len(members) >= 20000 or total > MAX_SOURCE or item.name in members or not item.isfile()
                    or (item.name != 'manifest.json' and
                        (not item.name.startswith('source/') or not safe_source_name(item.name[7:])))):
                raise ValueError('invalid/bounded source snapshot entry')
            members[item.name] = item
        if 'manifest.json' not in members or members['manifest.json'].size > MAX_RECORD:
            raise ValueError('missing/oversized source manifest')
        manifest = json.load(archive.extractfile(members['manifest.json']))
        if (manifest != json.loads((path / 'source-manifest.json').read_text())
                or manifest['component'] != record['component']
                or manifest['source_sha256'] != record['source_sha256']
                or hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest() != record['source_sha256']
                or set(manifest['files']) != {x[7:] for x in members if x != 'manifest.json'}):
            raise ValueError('source snapshot does not match build record')
        for name, spec in manifest['files'].items():
            member = members['source/' + name]
            if spec['mode'] not in (0o644, 0o755) or member.mode != spec['mode']:
                raise ValueError('source snapshot mode mismatch')
            sha = hashlib.sha256()
            with archive.extractfile(member) as stream:
                while chunk := stream.read(1024**2): sha.update(chunk)
            if sha.hexdigest() != spec['sha256']:
                raise ValueError('source snapshot file hash mismatch')
    frontend = {name: spec['sha256'] for name, spec in manifest['files'].items()
                if name in ('frontend/package.json', 'frontend/yarn.lock', 'frontend/.yarnrc.yml')}
    return {'source_sha256': manifest['source_sha256'], 'files': len(manifest['files']),
            'declared_frontend_inputs': frontend,
            'effective_frontend_dependency_archives_verified': False}


def payload_proof(image, artifacts, prefix='free5gc'):
    if prefix not in ('free5gc', 'usr/local/bin'):
        raise ValueError('unsupported image payload prefix')
    if not artifacts or len(artifacts) > 20000:
        raise ValueError('missing/oversized build artifact inventory')
    expected = {}
    for name, value in artifacts.items():
        if not safe_source_name(name) or not SHA.fullmatch(value):
            raise ValueError('invalid build artifact path/hash')
        expected[prefix + '/' + name] = value
    found = {}
    with tarfile.open(image) as archive:
        saved = json.load(archive.extractfile('manifest.json'))[0]
        for name in saved['Layers']:
            changes, removed, blocked = {}, set(), set()
            with tarfile.open(fileobj=archive.extractfile(name), mode='r|*') as layer:
                for member in layer:
                    path = safe_name(member.name)
                    parent, _, base = path.rpartition('/')
                    if base.startswith('.wh.'):
                        prefix = (parent + '/') if parent else ''
                        if base == '.wh..wh..opq':
                            removed.update(x for x in expected if x.startswith(prefix))
                        else:
                            target = prefix + base[4:]
                            removed.update(x for x in expected if x == target or x.startswith(target + '/'))
                        continue
                    # An ancestor replaced by a non-directory invalidates any
                    # lower-layer payload; never follow links out of the image.
                    if not member.isdir():
                        removed.update(x for x in expected if x.startswith(path + '/'))
                        blocked.update(x for x in expected if x.startswith(path + '/'))
                    if path not in expected:
                        continue
                    if not member.isfile():
                        changes[path] = None; continue
                    if member.size > MAX_SOURCE:
                        raise ValueError('build payload exceeds size limit')
                    sha = hashlib.sha256()
                    with layer.extractfile(member) as stream:
                        while chunk := stream.read(1024**2): sha.update(chunk)
                    changes[path] = sha.hexdigest()
            for path in removed: found.pop(path, None)
            if blocked & changes.keys():
                raise ValueError('ambiguous non-directory payload ancestor in layer')
            found.update(changes)
    if found != expected:
        raise ValueError('saved image payload differs from build artifact hashes')
    return len(expected)


def frontend_proof(path, record):
    expected = record.get('frontend_sources', {})
    if not expected:
        return {'dependency_archives': 0, 'effective_inputs_collected': False}
    if record['component'] != 'free5gc-webui' or len(expected) > 10000:
        raise ValueError('unexpected frontend sources')
    total = 0
    for name, spec in expected.items():
        if not (name in ('frontend-locks/package.json', 'frontend-locks/yarn.lock', 'frontend-locks/.yarnrc.yml', 'frontend-cache/.gitignore')
                or re.fullmatch(r'frontend-cache/@?[a-zA-Z0-9][a-zA-Z0-9._-]*\.zip', name)):
            raise ValueError('unsafe frontend evidence path')
        if not SHA.fullmatch(spec['sha256']) or type(spec['size']) is not int or not 0 <= spec['size'] <= 64 * 1024**2:
            raise ValueError('invalid frontend evidence size/hash')
        total += spec['size']
    if total > 480 * 1024**2:
        raise ValueError('frontend sources exceed size limit')
    archive_path = path / 'frontend-sources.tar'
    if archive_path.stat().st_size > MAX_SOURCE:
        raise ValueError('frontend archive exceeds size limit')
    if record.get('frontend_sources_archive_sha256') and digest(archive_path) != record['frontend_sources_archive_sha256']:
        raise ValueError('frontend archive hash mismatch')
    found, packages, notices, missing_notices = set(), 0, 0, []
    with tarfile.open(archive_path) as archive:
        for member in archive:
            name = safe_name(member.name)
            if member.isdir() and name in ('frontend-locks', 'frontend-cache'):
                continue
            if not member.isfile() or name not in expected or name in found or member.size != expected[name]['size']:
                raise ValueError('unexpected frontend archive entry')
            raw = archive.extractfile(member).read(64 * 1024**2 + 1)
            if len(raw) != member.size or hashlib.sha256(raw).hexdigest() != expected[name]['sha256']:
                raise ValueError('frontend input differs from build record')
            found.add(name)
            if name.endswith('.zip'):
                packages += 1
                with zipfile.ZipFile(io.BytesIO(raw)) as package:
                    members = package.infolist()
                    if len(members) > MAX_FRONTEND_ZIP_ENTRIES or sum(x.file_size for x in members) > 512 * 1024**2:
                        raise ValueError('frontend package ZIP exceeds bounds')
                    matches = [x for x in members if re.match(r'(?i)^(license|licence|copying|notice)([._-].*)?$', x.filename.rsplit('/', 1)[-1])]
                    notices += len(matches)
                    if not matches: missing_notices.append(name)
    if found != set(expected) or not packages or not {'frontend-locks/package.json', 'frontend-locks/yarn.lock', 'frontend-locks/.yarnrc.yml'} <= found:
        raise ValueError('incomplete frontend archive/locks')
    return {'dependency_archives': packages, 'effective_inputs_collected': True,
            'embedded_notice_paths': notices, 'archives_without_detected_notice': missing_notices,
            'license_review_complete': False, 'archive_sha256': digest(archive_path)}


def fetch_frontend(vm, relative, path, record):
    if not record.get('frontend_sources'):
        return
    target = path / 'frontend-sources.tar'
    if record.get('frontend_sources_archive_sha256'):
        fetch(vm, relative + '/frontend-sources.tar', target, MAX_SOURCE)
    else:
        # Older records bind individual cache files. Package those exact inputs
        # read-only; frontend_proof must verify the complete recorded set.
        if not BUILD_PATH.fullmatch(relative):
            raise ValueError('unexpected frontend build path')
        with target.open('xb') as stream:
            target.chmod(0o600)
            vm.ssh('sudo tar -C ' + shlex.quote('/opt/srv6-mup-compact/.lab/runtime/' + relative + '/provenance')
                   + ' -cf - -- frontend-locks frontend-cache', stdout=stream, timeout=120)
        if target.stat().st_size > MAX_SOURCE:
            raise ValueError('frontend archive exceeds size limit')


def verify_build(path, component, identifier, image):
    record = json.loads((path / 'build.json').read_text())
    if record.get('component') != component or record.get('image_id') != identifier or record.get('clean_runtime') is not True:
        raise ValueError('build record does not identify selected clean-runtime NF image')
    for name, expected in [('build.sh', record['build_script_sha256']),
                            ('image/Dockerfile', record['runtime_recipe_sha256'])]:
        if digest(path / name) != expected:
            raise ValueError('build script/recipe hash mismatch')
    if set(record['effective_module_locks']) != {'go.mod', 'go.sum'}:
        raise ValueError('missing/unsupported effective module locks')
    for name, expected in record['effective_module_locks'].items():
        if digest(path / 'provenance' / name) != expected:
            raise ValueError('effective module lock hash mismatch')
    proof = source_proof(path, record)
    proof['image_payload_files_verified'] = payload_proof(image, record['artifacts'])
    proof['frontend'] = frontend_proof(path, record)
    proof['effective_frontend_dependency_archives_verified'] = proof['frontend']['effective_inputs_collected']
    return {'component': component, 'image_id': identifier, **proof,
            'status': 'collected-unreviewed', 'distribution_approved': False,
            'files_sha256': {p.relative_to(path).as_posix(): digest(p) for p in path.rglob('*') if p.is_file()}}


def collect_common(audit, vm, directory, candidate):
    if set(candidate.get('common_build_records', {})) != set(COMMON):
        return {'status': 'missing-build-time-snapshots', 'collection_complete': False}
    result, artifacts, bootstrap = [], {}, None
    for component in COMMON:
        relative = candidate['common_build_records'][component]
        if not re.fullmatch(COMMON_PATH + '/payload.json', relative) or relative.split('/')[-2] != component:
            raise ValueError('unexpected common-runtime source location')
        prefix = '/'.join(relative.split('/')[:2])
        if bootstrap is not None and prefix != bootstrap:
            raise ValueError('mixed common-runtime build attempts')
        bootstrap = prefix
        relative = relative.removesuffix('/payload.json')
        path = directory / 'common' / component
        for name in ('payload.json', 'source.tar', 'source-manifest.json', 'build.sh'):
            fetch(vm, relative + '/' + name, path / name, MAX_SOURCE if name == 'source.tar' else MAX_RECORD)
        record = json.loads((path / 'payload.json').read_text())
        if record['component'] != component or digest(path / 'build.sh') != record['build_script_sha256']:
            raise ValueError('common build script/component differs from record')
        modules = record['effective_module_locks']
        if set(modules) != (set() if component == 'ueransim' else {'go.mod', 'go.sum'}):
            raise ValueError('invalid common effective module locks')
        for name, sha in modules.items():
            fetch(vm, relative + '/provenance/' + name, path / 'provenance' / name)
            if digest(path / 'provenance' / name) != sha:
                raise ValueError('common effective lock hash mismatch')
        proof = source_proof(path, record)
        if set(artifacts) & record['artifacts'].keys():
            raise ValueError('duplicate common-runtime payload')
        artifacts.update(record['artifacts'])
        result.append({'component': component, **proof,
                       'files_sha256': {p.relative_to(path).as_posix(): digest(p) for p in path.rglob('*') if p.is_file()}})
    if artifacts != candidate['binary_sha256']:
        raise ValueError('common payload records differ from candidate')
    recipe = candidate['common_runtime_recipe']
    if recipe != bootstrap + '/image/Dockerfile':
        raise ValueError('unexpected common runtime recipe location')
    for name in ('image/Dockerfile', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        fetch(vm, bootstrap + '/' + name, directory / 'common' / name)
    if digest(directory / 'common/image/Dockerfile') != candidate['common_runtime_recipe_sha256']:
        raise ValueError('common runtime recipe changed')
    matched = payload_proof(audit / candidate['image_id'][7:] / 'image.tar', artifacts, 'usr/local/bin')
    return {'status': 'collected-unreviewed', 'collection_complete': True, 'components': result,
            'image_id': candidate['image_id'], 'image_payload_files_verified': matched,
            'files_sha256': {name: digest(directory / 'common' / name) for name in ('image/Dockerfile', 'LICENSE', 'THIRD_PARTY_NOTICES.md')},
            'distribution_approved': False}


def collect(audit, vm):
    inputs = inventory(audit, 'runtime')  # Reverify saved immutable image evidence.
    vm.owned()
    parent = ROOT / '.lab/build-sources'
    if parent.is_symlink() or (ROOT / '.lab').is_symlink():
        raise ValueError('symlink private output directory')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    result = {'schema_version': 1, 'complete': False, 'collection_complete': False,
              'publication_approved': False, 'audit_manifest_sha256': inputs['audit_manifest_sha256'],
              'implementation_files_sha256': {name: digest(Path(__file__).parent / name) for name in
                  ('image_build_sources.py', 'image_sources.py', 'image_source_layers.py', 'compact_develop.py', 'compact_audit.py', 'compact_vm.py')},
              'builds': [], 'pending': ['native/eBPF linking and generated-input correspondence review',
                  'frontend dependency license and build-correspondence review',
                  'toolchain sources and build environment correspondence',
                  'source/archive privacy, licenses/notices and linking review',
                  'signed provenance and reproducible-build verification',
                  'explicit human publication approval']}
    try:
        fetch(vm, 'candidate.json', directory / 'candidate.json')
        candidate = json.loads((directory / 'candidate.json').read_text())
        result['candidate_sha256'] = digest(directory / 'candidate.json')
        selected = {image['image_id']: image for image in inputs['images']}
        if candidate['image_id'] not in selected:
            raise ValueError('guest common runtime differs from audited candidate')
        for nf in NF_NAMES:
            component = 'free5gc-' + nf
            path = directory / component
            try:
                identifier = candidate['nf_images'][component]
                roles = selected.get(identifier, {}).get('roles', [])
                if component not in roles and 'bootstrap:' + component not in roles:
                    raise ValueError('guest NF differs from audited candidate')
                build = candidate['nf_build_records'][component]
                if not re.fullmatch(BUILD_PATH.pattern + '/build.json', build):
                    raise ValueError('invalid NF build record location')
                relative = build.removesuffix('/build.json')
                for name in ('build.json', 'build.sh', 'source.tar', 'source-manifest.json',
                             'image/Dockerfile', 'provenance/go.mod', 'provenance/go.sum'):
                    fetch(vm, relative + '/' + name, path / name, MAX_SOURCE if name == 'source.tar' else MAX_RECORD)
                fetch_frontend(vm, relative, path, json.loads((path / 'build.json').read_text()))
                record = verify_build(path, component, identifier, audit / identifier[7:] / 'image.tar')
            except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError, tarfile.TarError) as error:
                record = {'component': component, 'status': 'collection-failed', 'reason': str(error)}
            result['builds'].append(record)
            atomic(directory / 'build-sources.json', result)
            print(component + ': ' + record['status'], flush=True)
        result['common_runtime'] = collect_common(audit, vm, directory, candidate)
        if result['common_runtime']['collection_complete'] is not True:
            result['pending'].append('common-runtime build-time snapshots missing in this candidate')
        result['nf_collection_complete'] = all(x['status'] == 'collected-unreviewed' for x in result['builds'])
        result['frontend_collection_complete'] = any(x['component'] == 'free5gc-webui'
            and x.get('frontend', {}).get('effective_inputs_collected') is True for x in result['builds'])
        fetch(vm, 'candidate.json', directory / 'candidate-after.json')
        if digest(directory / 'candidate-after.json') != result['candidate_sha256']:
            raise ValueError('candidate changed during evidence collection')
        result['complete'] = True
        result['collection_complete'] = (result['nf_collection_complete'] and result['common_runtime']['collection_complete']
                                         and result['frontend_collection_complete'])
    finally:
        atomic(directory / 'build-sources.json', result)
        print('Private compact build materials: ' + str(directory), flush=True)
    if not result['collection_complete']:
        raise ValueError('compact build evidence gaps remain; see private build-sources.json')
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit_directory', type=Path)
    parser.add_argument('--config', required=True, type=Path, help='explicit owned VM configuration; VM must already be running')
    args = parser.parse_args()
    os.umask(0o077)
    collect(args.audit_directory, VM(load(args.config)))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError, tarfile.TarError) as error:
        sys.exit(f'ERROR: {error}')
