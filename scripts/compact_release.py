"""Pinned prebuilt images for the SAME compact topology; never publish/approve.

A manifest is trusted repository input, not a signature or a license verdict.
There is intentionally no default release until redistribution review finishes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

from compact_config import ROOT, locks
from compact_vm import digest, output, run

DEFAULT = ROOT / 'config/compact-release.json'
HEX = re.compile(r'[a-f0-9]{64}')
ID = re.compile(r'sha256:[a-f0-9]{64}')
# Distribution scope is intentionally GHCR only, with no tags/credentials/URLs.
REF = re.compile(r'ghcr\.io/[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def compatibility():
    """Bind guest orchestration, recipes, external source/toolchain locks, templates.

    Own Go sources are separate so an existing deployment remains editable.
    Exclude the release manifest itself to avoid a self-referential hash.
    """
    names = json.loads((ROOT / 'config/public-source.json').read_text())['files']
    selected = [name for name in names if name.startswith(('scripts/', 'containers/', 'ansible/'))
                or name in ('config/versions.lock.yml', 'config/compact-dependencies.json',
                            'config/compact.example.yml', 'config/lab.example.yml')]
    return fingerprint({name: digest(ROOT / name) for name in sorted(selected)})


def source_hash():
    from compact_develop import safe_source_name
    paths = [ROOT / 'go.mod', ROOT / 'go.sum']
    for directory in ('api', 'cmd', 'internal'):
        paths.extend((ROOT / directory).rglob('*'))
    files = {}
    for path in sorted(set(paths)):
        name = path.relative_to(ROOT).as_posix()
        if not safe_source_name(name):
            continue
        if path.is_symlink():
            raise ValueError('symlink in release source')
        if path.is_file():
            files[name] = {'sha256': digest(path), 'mode': 0o755 if path.stat().st_mode & 0o111 else 0o644}
    # Same hash as compact_develop.snapshot, without creating a source archive.
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def builder_inputs():
    config = locks()
    return {'recipe': digest(ROOT / 'containers/compact-builder.Dockerfile'),
            'base': config['compact']['runtime_base'],
            'linux_libc_dev': config['compact']['builder_linux_libc_dev'],
            'toolchains': {key: config['toolchains'][key] for key in ('go', 'node', 'yarn')}}


def image_spec(value):
    if (not isinstance(value, dict) or set(value) != {'reference', 'image_id'}
            or not isinstance(value['reference'], str) or not REF.fullmatch(value['reference'])
            or any(part in ('', '.', '..') for part in value['reference'].split('@')[0].split('/'))
            or not isinstance(value['image_id'], str) or not ID.fullmatch(value['image_id'])):
        raise ValueError('release image must pin a GHCR digest and immutable image ID')
    return value


def validate(data, *, require_source=False):
    from compact_compose import CORE_SERVICES
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'release'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1):
        raise ValueError('invalid release manifest envelope')
    spec = data['release']
    if spec is None:
        raise ValueError('no published release is configured; use up --build for a local candidate')
    fields = {'version', 'platform', 'compatibility_sha256', 'source_sha256', 'runtime',
              'nf_images', 'builder', 'database', 'dashboard_snapshot_protocol'}
    if (not isinstance(spec, dict) or set(spec) != fields
            or not isinstance(spec['version'], str)
            or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?', spec['version'])
            or spec['platform'] != 'linux/amd64'
            or type(spec['dashboard_snapshot_protocol']) is not int
            or spec['dashboard_snapshot_protocol'] != 1):
        raise ValueError('invalid release version/platform/protocol/fields')
    for key in ('compatibility_sha256', 'source_sha256'):
        if not isinstance(spec[key], str) or not HEX.fullmatch(spec[key]):
            raise ValueError('invalid release source fingerprint')
    if spec['compatibility_sha256'] != compatibility():
        raise ValueError('release orchestration/source/toolchain locks differ; use matching release source')
    if require_source and spec['source_sha256'] != source_hash():
        raise ValueError('release Go source differs; install from matching source before editing/rebuilding')
    image_spec(spec['runtime'])
    names = set(CORE_SERVICES) - {'db'}
    if not isinstance(spec['nf_images'], dict) or set(spec['nf_images']) != names:
        raise ValueError('release must contain all eleven NF images and must not mirror MongoDB')
    for image in spec['nf_images'].values():
        image_spec(image)
    if spec['database'] != locks()['compact']['database']['image']:
        raise ValueError('release MongoDB must be the locked upstream image')
    if spec['builder'] is not None:
        if not isinstance(spec['builder'], dict) or set(spec['builder']) != {'image', 'inputs'}:
            raise ValueError('invalid builder release')
        image_spec(spec['builder']['image'])
        if spec['builder']['inputs'] != builder_inputs():
            raise ValueError('release builder recipe/toolchains differ')
    return spec


def read(path=None, *, require_source=False):
    path = Path(path) if path else DEFAULT
    if path.stat().st_size > 128 * 1024:
        raise ValueError('release manifest exceeds size limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate release manifest key')
            result[key] = value
        return result
    data = json.loads(path.read_text(), object_pairs_hook=unique)
    validate(data, require_source=require_source)
    return data


def inspect_image(spec):
    rows = json.loads(output(['docker', 'image', 'inspect', spec['reference']]))
    if (len(rows) != 1 or rows[0]['Id'] != spec['image_id'] or rows[0]['Os'] != 'linux'
            or rows[0]['Architecture'] != 'amd64'
            or spec['reference'] not in (rows[0].get('RepoDigests') or [])):
        raise ValueError('pulled image identity/platform differs from release manifest')
    return spec['image_id']


def acquire(spec):
    image_spec(spec)
    try:
        return inspect_image(spec)
    except subprocess.CalledProcessError:
        # An uncached reference causes inspect to fail. Pull must succeed and
        # be rechecked; metadata mismatches never trigger silent replacement.
        run(['docker', 'pull', '--platform', 'linux/amd64', spec['reference']])
        return inspect_image(spec)


def selection_guard(data):
    """Reject baseline replacement before kernel setup can stop a live core."""
    from compact_runtime import STATE
    from compact_develop import DEV
    from compact_database import idle
    idle(STATE)
    if (DEV / 'pending.json').exists():
        raise ValueError('unfinished activation; recover with up before release selection')
    target = STATE / 'candidate.json'
    if target.exists():
        previous = json.loads(target.read_text())
        if previous.get('release_manifest') != data:
            raise ValueError('refusing to replace an existing baseline; use a separate compact VM profile')


def install(config, data):
    """Initial selection only. No live baseline upgrades, DB migration or build."""
    from compact_runtime import STATE, render, checkout, database_selection
    from compact_compose import candidate_selection
    from compact_develop import atomic, overrides
    spec = validate(data, require_source=True)
    selection_guard(data)
    target = STATE / 'candidate.json'
    database = database_selection(create=False)
    if database['image'] != spec['database']:
        raise ValueError('release requires a fresh/current database; no automatic major migration')
    runtime = acquire(spec['runtime'])
    nfs = {name: acquire(image) for name, image in sorted(spec['nf_images'].items())}
    # Deliberately never acquire spec['builder'] here.
    candidate = {'image': spec['runtime']['reference'], 'image_id': runtime,
                 'image_set_schema': 1, 'nf_images': nfs, 'dashboard_snapshot_protocol': 1,
                 'release_manifest': data, 'release_manifest_sha256': fingerprint(data),
                 'publication': 'pinned-manifest-not-a-distribution-approval'}
    staging = STATE / 'releases' / uuid.uuid4().hex
    staging.mkdir(parents=True)
    render(config, checkout('free5gc_compose'), staging / 'config', runtime,
           candidate_selection(candidate, overrides()), database)
    run(['docker', 'compose', '-p', 'srv6-mup-compact', '-f', staging / 'config/compose.yml', 'config', '--quiet'])
    atomic(staging / 'candidate.json', candidate)
    # All pulls and validation must succeed before changing the active baseline.
    atomic(target, candidate)


def release_builder(candidate, desired):
    data = candidate['release_manifest']
    spec = validate(data)
    if candidate.get('release_manifest_sha256') != fingerprint(data):
        raise ValueError('stored release manifest fingerprint mismatch')
    if spec['builder'] is None:
        raise ValueError('no reviewed builder configured; explicitly use rebuild --local-builder')
    entry = spec['builder']
    if entry['inputs'] != desired:
        raise ValueError('release builder does not match local build inputs')
    return {'inputs': desired, 'image_id': acquire(entry['image']),
            'reference': entry['image']['reference'], 'release_manifest_sha256': fingerprint(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest')
    parser.add_argument('--inputs', action='store_true', help='print source/build fingerprints, without approval')
    parser.add_argument('--pull', choices=['runtime', 'builder'], help='verify registry images without running them')
    args = parser.parse_args()
    if args.inputs:
        if args.pull or args.manifest:
            parser.error('--inputs cannot be combined with manifest/pull')
        print(json.dumps({'compatibility_sha256': compatibility(), 'source_sha256': source_hash(),
                          'builder_inputs': builder_inputs()}, indent=2))
        return
    data = read(args.manifest, require_source=True)
    spec = data['release']
    if args.pull == 'runtime':
        for image in [spec['runtime'], *spec['nf_images'].values()]:
            acquire(image)
    elif args.pull == 'builder':
        if spec['builder'] is None:
            raise ValueError('no reviewed builder configured')
        acquire(spec['builder']['image'])
    print(json.dumps({'version': spec['version'], 'manifest_sha256': fingerprint(data),
                      'builder_available': spec['builder'] is not None, 'verified_pull': args.pull}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'ERROR: {error}')
