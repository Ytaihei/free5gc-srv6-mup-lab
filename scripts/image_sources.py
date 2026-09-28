#!/usr/bin/env python3
"""Collect exact Ubuntu source packages for PRIVATE image-distribution review.

No installation, source extraction, source execution, registry upload, license
approval or CVE suppression. HTTPS publication records identify the descriptor;
its SHA-256/size fields bind the downloaded archives. This is NOT GPG verification.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from threading import Lock
import urllib.error
import urllib.parse
import urllib.request

from compact_config import ROOT
from compact_vm import digest, write
from compact_audit import IMAGE_ID, verify_archive

API = 'https://api.launchpad.net/devel/ubuntu/+archive/primary'
SERIES = 'https://api.launchpad.net/devel/ubuntu/noble'
NAME = re.compile(r'[a-z0-9][a-z0-9+.-]+')
VERSION = re.compile(r'[0-9][a-zA-Z0-9.+:~\-]*')
FILE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9+._~:-]*')
SHA = re.compile(r'[a-f0-9]{64}')
MAX_META = 8 * 1024**2
MAX_FILE = 2 * 1024**3
DOWNLOAD_LOCK = Lock()


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    write(temporary, json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def official_url(url):
    if not isinstance(url, str):
        raise ValueError('invalid official source URL')
    item = urllib.parse.urlsplit(url)
    host = item.hostname or ''
    allowed = (host in ('api.launchpad.net', 'launchpad.net', 'launchpadlibrarian.net')
               or re.fullmatch(r'[a-z0-9-]+\.launchpadlibrarian\.net', host))
    if (item.scheme != 'https' or not allowed or item.port not in (None, 443)
            or item.username or item.password or item.fragment or any(c.isspace() for c in url)):
        raise ValueError('source URL is not allowlisted HTTPS Launchpad infrastructure')
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        official_url(new_url)
        return super().redirect_request(request, fp, code, message, headers, new_url)


def response(url):
    official_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'srv6-mup-private-source-review/1'})
    # No netrc, auth handler or cookies. Every redirect is validated before GET.
    return urllib.request.build_opener(OfficialRedirect()).open(request, timeout=60)


def metadata(url):
    with response(url) as stream:
        raw = stream.read(MAX_META + 1)
    if len(raw) > MAX_META:
        raise ValueError('source metadata exceeds size limit')
    return raw


def group(roles):
    if not isinstance(roles, list) or not roles or any(not isinstance(x, str) for x in roles):
        raise ValueError('missing image roles')
    if set(roles) == {'builder'}:
        return 'builder'
    if set(roles) == {'db'}:
        return 'upstream'
    if set(roles) & {'builder', 'db'}:
        raise ValueError('mixed runtime/builder/database image roles need review')
    from compact_audit import SERVICES
    allowed = (SERVICES - {'db'}) | {'bootstrap'} | {'bootstrap:' + s for s in SERVICES - {'db'}}
    if set(roles) - allowed:
        raise ValueError('unknown audited image role')
    return 'runtime'


def package_version(package, prefix=''):
    """Trivy separates Debian epoch/version/revision; never drop distro patches.

    Source and binary epochs may differ (e.g. gcc-defaults). A missing source
    epoch must NOT inherit the binary epoch.
    """
    version = package.get(prefix + 'Version')
    release = package.get(prefix + 'Release')
    epoch = package.get(prefix + 'Epoch', 0)
    if (not isinstance(version, str) or not VERSION.fullmatch(version)
            or type(epoch) is not int or epoch < 0
            or release is not None and (not isinstance(release, str)
                                       or not re.fullmatch(r'[a-zA-Z0-9+.~]+', release))):
        raise ValueError('missing/invalid exact package version fields')
    if epoch:
        if ':' in version:
            raise ValueError('ambiguous package epoch')
        version = str(epoch) + ':' + version
    if release:
        version += '-' + release
    return version


def inventory(directory, scope, all_layers=False):
    if scope not in ('runtime', 'builder', 'upstream', 'all'):
        raise ValueError('invalid image scope')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if (manifest.get('schema_version') != 1 or manifest.get('complete') is not True
            or manifest.get('selection_unchanged') is not True
            or manifest.get('database_unchanged') is not True or not manifest.get('images')):
        raise ValueError('requires complete immutable-image audit with unchanged selection/database')
    sources, images, unresolved, seen, layer_cache = {}, [], [], set(), {}
    for item in manifest['images']:
        identifier = item['image_id']
        if not isinstance(identifier, str) or not IMAGE_ID.fullmatch(identifier) or identifier in seen:
            raise ValueError('invalid/duplicate image identity')
        seen.add(identifier)
        category = group(item['roles'])
        if scope != 'all' and scope != category:
            continue
        if item.get('complete') is not True:
            raise ValueError('incomplete selected image audit')
        path = directory / identifier.removeprefix('sha256:')
        for filename, field in [('image.tar', 'archive_sha256'), ('scan.json', 'report_sha256'),
                                ('sbom.cdx.json', 'sbom_sha256')]:
            artifact = path / filename
            if artifact.is_symlink() or digest(artifact) != item[field]:
                raise ValueError('audited artifact changed: ' + filename)
        proof = verify_archive(path / 'image.tar', identifier)
        report = json.loads((path / 'scan.json').read_text())
        if (report.get('ArtifactType') != 'container_image'
                or report.get('Metadata', {}).get('ImageID') != proof['config_id']
                or report.get('Metadata', {}).get('DiffIDs') != proof['diff_ids']):
            raise ValueError('scanner does not match verified image/config/layers')
        image = {'image_id': identifier, 'roles': item['roles'], 'group': category,
                 'archive_sha256': item['archive_sha256'], 'report_sha256': item['report_sha256'],
                 'sbom_sha256': item['sbom_sha256'], 'os_packages': 0, 'source_keys': []}
        images.append(image)
        final_packages = set()
        operating_system = report.get('Metadata', {}).get('OS', {})
        if operating_system.get('Family') != 'ubuntu' or operating_system.get('Name') != '24.04':
            unresolved.append({'image_id': identifier, 'reason': 'unsupported-OS; only Ubuntu 24.04 is implemented'})
            continue
        for result in report.get('Results', []):
            if result.get('Class') != 'os-pkgs':
                continue
            if result.get('Type') != 'ubuntu':
                raise ValueError('unexpected OS package type')
            for package in result.get('Packages') or []:
                image['os_packages'] += 1
                name = package.get('SrcName')
                try:
                    version = package_version(package, 'Src')
                    binary_version = package_version(package)
                    if not isinstance(name, str) or not NAME.fullmatch(name):
                        raise ValueError('missing source name')
                except ValueError:
                    unresolved.append({'image_id': identifier, 'binary': package.get('Name'),
                                       'version': package.get('Version'), 'reason': 'missing/invalid exact source identity'})
                    continue
                key = hashlib.sha256((name + '\0' + version).encode()).hexdigest()
                source = sources.setdefault(key, {'key': key, 'name': name, 'version': version, 'binaries': []})
                source['binaries'].append({'image_id': identifier, 'name': package['Name'],
                                           'version': binary_version, 'arch': package.get('Arch')})
                image['source_keys'].append(key)
                final_packages.add((package['Name'], binary_version, name, version))
        if all_layers:
            from image_source_layers import image_layers
            layers = image_layers(path / 'image.tar', proof, layer_cache)
            recorded = set()
            image['package_metadata_layers'] = []
            for layer in layers:
                image['package_metadata_layers'].append({
                    'layer_digest': layer['layer_digest'], 'diff_id': layer['diff_id'],
                    'metadata': [{k: record[k] for k in ('path', 'sha256')} for record in layer['metadata']]})
                unresolved.extend({'image_id': identifier, 'layer_digest': layer['layer_digest'], **issue}
                                  for issue in layer['unresolved'])
                for record in layer['metadata']:
                    for package in record['packages']:
                        name, version = package['source'], package['source_version']
                        if package['relationship'] == 'source':
                            recorded.add((package['name'], package['version'], name, version))
                        key = hashlib.sha256((name + '\0' + version).encode()).hexdigest()
                        source = sources.setdefault(key, {'key': key, 'name': name, 'version': version, 'binaries': []})
                        source['binaries'].append({'image_id': identifier, 'name': package['name'],
                            'version': package['version'], 'arch': package['arch'],
                            'relationship': package['relationship'], 'layer_digest': layer['layer_digest'],
                            'metadata_path': record['path'], 'metadata_sha256': record['sha256']})
                        image['source_keys'].append(key)
            for name, version, _, _ in sorted(final_packages - recorded):
                unresolved.append({'image_id': identifier, 'binary': name, 'version': version,
                                   'reason': 'final scanner identity absent from all layer package metadata'})
        image['source_keys'] = sorted(set(image['source_keys']))
        if image['os_packages'] == 0:
            unresolved.append({'image_id': identifier, 'reason': 'no OS package inventory'})
    if not images:
        raise ValueError('no images selected for requested scope')
    return {'audit_manifest_sha256': digest(directory / 'manifest.json'), 'scope': scope,
            'os_inventory': 'all-layer-dpkg-metadata' if all_layers else 'final-filesystem',
            'images': images, 'sources': sorted(sources.values(), key=lambda s: (s['name'], s['version'])),
            'unresolved': unresolved}


def publication_url(name, version):
    if not NAME.fullmatch(name) or not VERSION.fullmatch(version):
        raise ValueError('invalid source package name/version')
    query = urllib.parse.urlencode({'ws.op': 'getPublishedSources', 'source_name': name,
                                    'version': version, 'exact_match': 'true', 'distro_series': SERIES,
                                    'ws.size': '100'})
    return API + '?' + query


def select_publication(raw, name, version):
    rows = json.loads(raw)
    if rows.get('next_collection_link') or len(rows.get('entries', [])) > 100:
        raise ValueError('ambiguous/oversized source publication collection')
    entries = []
    for entry in rows.get('entries', []):
        if (entry.get('source_package_name') != name or entry.get('source_package_version') != version
                or entry.get('archive_link') != API or entry.get('distro_series_link') != SERIES):
            raise ValueError('Launchpad returned a different source/version/archive/series')
        if entry.get('status') not in ('Published', 'Superseded', 'Obsolete'):
            continue
        if not re.fullmatch(re.escape(API) + r'/\+sourcepub/[0-9]+', entry.get('self_link', '')):
            raise ValueError('unexpected publication identity')
        entries.append(entry)
    if not entries:
        raise ValueError('exact source version unavailable in official Ubuntu archive')
    # Re-publications of the SAME version can occur in Security/Updates. Do
    # not guess a different version or follow a similarly named PPA.
    entries.sort(key=lambda x: (x['status'] != 'Published', x['self_link']))
    return entries[0]


def publications(name, version):
    url = publication_url(name, version)
    raw = metadata(url)
    return select_publication(raw, name, version), {'url': url, 'sha256': hashlib.sha256(raw).hexdigest()}, raw


def descriptor(raw, name, version):
    text = raw.decode('utf-8').replace('\r\n', '\n')
    if text.startswith('-----BEGIN PGP SIGNED MESSAGE-----\n'):
        try:
            body = text.split('\n\n', 1)[1]
            text, signature = body.split('\n-----BEGIN PGP SIGNATURE-----', 1)
            if not signature.rstrip().endswith('-----END PGP SIGNATURE-----'):
                raise ValueError('incomplete signature envelope')
        except (IndexError, ValueError):
            raise ValueError('invalid clearsigned descriptor') from None
        text = '\n'.join(line[2:] if line.startswith('- ') else line for line in text.splitlines())
    fields, current = {}, None
    for line in text.splitlines():
        if not line:
            continue
        if line[0].isspace():
            if current is None:
                raise ValueError('orphan descriptor continuation')
            fields[current] += '\n' + line.strip()
        else:
            key, separator, value = line.partition(':')
            if not separator or not re.fullmatch('[A-Za-z][A-Za-z0-9-]*', key) or key.lower() in fields:
                raise ValueError('invalid/duplicate source descriptor field')
            current = key.lower(); fields[current] = value.strip()
    if fields.get('source') != name or fields.get('version') != version:
        raise ValueError('descriptor source/version mismatch')
    if fields.get('format') not in ('1.0', '3.0 (quilt)', '3.0 (native)'):
        raise ValueError('unsupported source format')
    files = {}
    for line in fields.get('checksums-sha256', '').splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if (len(parts) != 3 or not SHA.fullmatch(parts[0]) or not parts[1].isdigit()
                or not 0 < int(parts[1]) <= MAX_FILE or not FILE.fullmatch(parts[2]) or parts[2] in files):
            raise ValueError('invalid/duplicate source checksum, size or filename')
        files[parts[2]] = {'sha256': parts[0], 'size': int(parts[1])}
    if not files:
        raise ValueError('source descriptor has no SHA-256 files')
    return files


def download(url, directory, expected, budget, local_source=None):
    # Metadata lookups may overlap. Serialize archive writes to preserve the
    # shared budget/free-space reserve and deduplication without cache races.
    with DOWNLOAD_LOCK:
        return download_locked(url, directory, expected, budget, local_source)


def download_locked(url, directory, expected, budget, local_source=None):
    official_url(url)
    if local_source is not None and (local_source.is_symlink() or not local_source.is_file()):
        raise ValueError('reused source blob must be a regular file, not a symlink')
    if not SHA.fullmatch(expected['sha256']) or not 0 < expected['size'] <= MAX_FILE:
        raise ValueError('invalid source download identity')
    target = directory / expected['sha256']
    if target.is_symlink():
        raise ValueError('refusing symlink source cache')
    if target.exists():
        if target.stat().st_size != expected['size'] or digest(target) != expected['sha256']:
            raise ValueError('cached source size/hash mismatch')
        return
    if expected['size'] > budget['remaining']:
        raise ValueError('source download budget exhausted')
    if shutil.disk_usage(directory).free < expected['size'] + 2 * 1024**3:
        raise ValueError('insufficient free space; preserve a 2 GiB reserve')
    # Reserve before the request: failed/interrupted files also consume the cap.
    # This is a conservative reservation, not a successful-download byte count.
    budget['remaining'] -= expected['size']
    temporary = directory / (expected['sha256'] + '.partial')
    if temporary.exists() or temporary.is_symlink():
        # An interrupted attempt is retained; retry uses a unique partial name.
        temporary = directory / (expected['sha256'] + '.' + os.urandom(6).hex() + '.partial')
    actual, size = hashlib.sha256(), 0
    with (local_source.open('rb') if local_source is not None else response(url)) as stream, temporary.open('xb') as destination:
        temporary.chmod(0o600)
        while chunk := stream.read(min(1024**2, expected['size'] - size + 1)):
            size += len(chunk)
            if size > expected['size']:
                raise ValueError('source response exceeds declared size')
            destination.write(chunk); actual.update(chunk)
    if size != expected['size'] or actual.hexdigest() != expected['sha256']:
        raise ValueError('source archive size/hash mismatch')
    os.replace(temporary, target)
    if local_source is not None:
        budget['reused_bytes'] = budget.get('reused_bytes', 0) + size


def source_urls(files_raw):
    urls = json.loads(files_raw)
    if not isinstance(urls, list) or not 1 < len(urls) <= 100:
        raise ValueError('invalid source file inventory')
    named = {}
    for value in urls:
        official_url(value)
        filename = urllib.parse.unquote(urllib.parse.urlsplit(value).path.rsplit('/', 1)[-1])
        if not FILE.fullmatch(filename) or filename in named:
            raise ValueError('unsafe/duplicate source filename')
        named[filename] = value
    dscs = [name for name in named if name.endswith('.dsc')]
    if len(dscs) != 1:
        raise ValueError('expected one source descriptor')
    return named, dscs[0]


def collect_source(source, directory, blobs, budget):
    entry, proof, raw = publications(source['name'], source['version'])
    write(directory / 'publication.json', raw.decode())
    url = entry['self_link'] + '?ws.op=sourceFileUrls'
    files_raw = metadata(url)
    write(directory / 'source-file-urls.json', files_raw.decode())
    named, dsc_name = source_urls(files_raw)
    dsc = metadata(named[dsc_name])
    write(directory / 'source.dsc', dsc.decode())
    expected = descriptor(dsc, source['name'], source['version'])
    if set(named) != set(expected) | {dsc_name}:
        raise ValueError('publication file list does not exactly match source descriptor')
    for name, spec in expected.items():
        download(named[name], blobs, spec, budget)
    return {'status': 'collected-unreviewed', 'source': source['name'], 'version': source['version'],
            'publication': entry['self_link'], 'publication_query': proof,
            'file_urls_sha256': hashlib.sha256(files_raw).hexdigest(),
            'descriptor': {'filename': dsc_name, 'url': named[dsc_name], 'sha256': hashlib.sha256(dsc).hexdigest()},
            'archives': [{**spec, 'filename': name, 'url': named[name]} for name, spec in sorted(expected.items())],
            'signature_verified': False, 'distribution_approved': False}


def cache_index(directories):
    try:
        return checked_cache_index(directories)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('malformed source cache manifest') from error


def checked_cache_index(directories):
    result, manifests = {}, []
    for directory in directories:
        directory = Path(directory).absolute()
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError('source cache directory cannot contain symlinks or traversal')
        path = directory / 'sources.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024**2:
            raise ValueError('invalid source cache manifest type/size')
        raw = path.read_bytes(); data = json.loads(raw)
        if data.get('schema_version') != 1 or data.get('complete') is not True:
            raise ValueError('only completed source collection loops can be reused')
        sha = hashlib.sha256(raw).hexdigest(); manifests.append(sha)
        inputs = {item['key']: (item['name'], item['version']) for item in data['inputs']['sources']}
        if len(inputs) != len(data['inputs']['sources']):
            raise ValueError('duplicate cached input identity')
        seen = set()
        for record in data['sources']:
            key = record['key']
            if not SHA.fullmatch(key) or key in seen:
                raise ValueError('invalid/duplicate cached source identity')
            seen.add(key)
            if record['status'] != 'collected-unreviewed':
                continue
            name, version = record['source'], record['version']
            if (not NAME.fullmatch(name) or not VERSION.fullmatch(version)
                    or key != hashlib.sha256((name + '\0' + version).encode()).hexdigest()
                    or inputs.get(key) != (name, version)):
                raise ValueError('cached source differs from its recorded input')
            if key in result:
                previous = result[key]['record']
                if previous['descriptor'] != record['descriptor'] or previous['archives'] != record['archives']:
                    raise ValueError('conflicting exact-version source caches')
                continue
            result[key] = {'directory': directory, 'manifest_sha256': sha, 'record': record}
    return result, sorted(set(manifests))


def reuse_source(source, cached, directory, blobs, budget):
    root, record = cached['directory'], cached['record']
    location = root / source['key']
    if location.is_symlink() or (root / 'source-blobs').is_symlink():
        raise ValueError('refusing symlink source cache content directory')
    evidence = {}
    for filename, expected in [('publication.json', record['publication_query']['sha256']),
                               ('source-file-urls.json', record['file_urls_sha256']),
                               ('source.dsc', record['descriptor']['sha256'])]:
        path = location / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_META:
            raise ValueError('invalid cached source evidence type/size')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('cached source evidence hash mismatch')
        evidence[filename] = raw
    name, version = source['name'], source['version']
    entry = select_publication(evidence['publication.json'], name, version)
    if (record['publication_query']['url'] != publication_url(name, version)
            or record['publication'] != entry['self_link']):
        raise ValueError('cached publication does not identify requested exact source')
    named, dsc_name = source_urls(evidence['source-file-urls.json'])
    expected = descriptor(evidence['source.dsc'], name, version)
    archives = [{**spec, 'filename': item, 'url': named.get(item)} for item, spec in sorted(expected.items())]
    if (set(named) != set(expected) | {dsc_name} or record['archives'] != archives
            or record['descriptor']['filename'] != dsc_name or record['descriptor']['url'] != named[dsc_name]):
        raise ValueError('cached source files differ from the exact descriptor/publication')
    for filename, raw in evidence.items():
        write(directory / filename, raw.decode())
    for item in archives:
        download(item['url'], blobs, item, budget, local_source=root / 'source-blobs' / item['sha256'])
    return {**record, 'reused_from_collection_sha256': cached['manifest_sha256'],
            'signature_verified': False, 'distribution_approved': False}


def collect(audit, scope, max_bytes, jobs=3, all_layers=False, reuse=()):
    if type(jobs) is not int or not 1 <= jobs <= 4:
        raise ValueError('source collection jobs must be between 1 and 4')
    inputs = inventory(audit, scope, all_layers)  # Verify all inputs before output/network work.
    cached, cache_manifests = cache_index(reuse)
    parent = ROOT / '.lab/distro-sources'
    if parent.is_symlink() or (ROOT / '.lab').is_symlink():
        raise ValueError('refusing symlink private output directory')
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    blobs = directory / 'source-blobs'; blobs.mkdir(mode=0o700)
    result = {'schema_version': 1, 'complete': False, 'collection_complete': False,
              'publication_approved': False, 'inputs': inputs, 'sources': [],
              'implementation_sha256': digest(Path(__file__)), 'metadata_jobs': jobs,
              'implementation_files_sha256': {name: digest(Path(__file__).parent / name) for name in
                  ('image_sources.py', 'image_source_layers.py', 'compact_audit.py', 'compact_vm.py')},
              'reused_collection_manifests': cache_manifests,
              'pending': ['exact license/notices and source correspondence review',
                          'non-distro native/eBPF/frontend/toolchain coverage',
                          'unrecorded payloads and lower-layer source/license correspondence review',
                          'signature verification and binary-to-source build correspondence',
                          'privacy review of collected source archives',
                          'vulnerability disposition and explicit human publication approval']}
    if not all_layers:
        result['pending'].append('all-layer package metadata inventory, including replaced package versions')
    budget = {'remaining': max_bytes}
    def worker(source):
        location = directory / source['key']; location.mkdir(mode=0o700)
        try:
            record = (reuse_source(source, cached[source['key']], location, blobs, budget)
                      if source['key'] in cached else collect_source(source, location, blobs, budget))
        except (ValueError, OSError, urllib.error.URLError, KeyError, TypeError, AttributeError) as error:
            record = {'status': 'collection-failed', 'error': type(error).__name__, 'reason': str(error)}
            # Diagnostics contain only official URLs/package names, never credentials.
        record['key'] = source['key']
        return record
    try:
        atomic(directory / 'sources.json', result)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(worker, source): source for source in inputs['sources']}
            try:
                for index, future in enumerate(as_completed(futures), 1):
                    source = futures[future]; record = future.result()
                    result['sources'].append(record)
                    print(f'[{index}/{len(inputs["sources"])}] {source["name"]}={source["version"]}: {record["status"]}', flush=True)
                    atomic(directory / 'sources.json', result)
            except BaseException:
                # Retain incomplete evidence; do not start queued work after an
                # interruption. In-flight calls still obey their socket limits.
                for future in futures:
                    future.cancel()
                raise
        result['complete'] = True
        result['collection_complete'] = not inputs['unresolved'] and all(r['status'] == 'collected-unreviewed' for r in result['sources'])
    finally:
        result['reserved_archive_bytes'] = max_bytes - budget['remaining']
        result['reused_archive_bytes'] = budget.get('reused_bytes', 0)
        atomic(directory / 'sources.json', result)
        print(f'Private exact-version source materials: {directory}', flush=True)
    if not result['collection_complete']:
        raise ValueError('source coverage gaps remain; see private sources.json; no version substitution was made')
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit_directory', type=Path)
    parser.add_argument('--scope', choices=['runtime', 'builder', 'upstream', 'all'], default='runtime')
    parser.add_argument('--download', action='store_true', help='fetch exact sources into private .lab state')
    parser.add_argument('--all-layers', action='store_true', help='also cover recorded dpkg versions in every saved layer')
    parser.add_argument('--reuse', type=Path, action='append', default=[],
                        help='reuse exact, reverified source records from a completed private collection')
    parser.add_argument('--max-gib', type=int, default=8, help='copied/downloaded archive limit, 1..32 GiB')
    parser.add_argument('--jobs', type=int, choices=range(1, 5), default=3,
                        help='concurrent metadata lookups, 1..4; archive writes are serialized')
    args = parser.parse_args()
    if not 1 <= args.max_gib <= 32:
        parser.error('--max-gib must be between 1 and 32')
    os.umask(0o077)
    if args.download:
        collect(args.audit_directory, args.scope, args.max_gib * 1024**3, args.jobs, args.all_layers, args.reuse)
    else:
        if args.reuse:
            parser.error('--reuse requires --download')
        data = inventory(args.audit_directory, args.scope, args.all_layers)
        print(json.dumps({'scope': args.scope, 'images': len(data['images']), 'source_packages': len(data['sources']),
                          'os_inventory': data['os_inventory'],
                          'unresolved': data['unresolved'], 'publication_approved': False}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, urllib.error.URLError) as error:
        sys.exit(f'ERROR: {error}')
