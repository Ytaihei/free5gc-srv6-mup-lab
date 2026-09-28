"""Private immutable-image evidence; never a publication or legal approval."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

import yaml

from compact_config import ROOT, locks, module
from image_distribution import classify, license_index
from compact_compose import CORE_SERVICES, ROLE_SERVICES, EXTRA_SERVICES, candidate_nf_images
from compact_vm import digest, write

SERVICES = set(CORE_SERVICES + ROLE_SERVICES + EXTRA_SERVICES)
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')
SCANNER = ROOT / 'scripts/verification-tool.sh'
PENDING = ['all-layer and image-history privacy review', 'corresponding sources and license notices',
           'C/C++ and embedded eBPF dependency coverage', 'guest OS and gtp5g scan',
           'legacy upstream NF images outside this selected/bootstrap inventory',
           'signed provenance, anonymous pulls and explicit publication approval']

# No docker exec/commit/export, mutable tag resolution or guest filesystem copy.
# Only image save later transfers bytes; container writable layers/volumes stay put.
SNAPSHOT = r'''
import json, subprocess
from pathlib import Path
def command(*args):
    return subprocess.check_output(args, text=True)
ids = command('docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=srv6-mup-compact').split()
if not ids:
    raise ValueError('no compact containers')
containers = []
for item in json.loads(command('docker', 'container', 'inspect', *ids)):
    labels = item['Config'].get('Labels') or {}
    if labels.get('com.docker.compose.project') != 'srv6-mup-compact':
        raise ValueError('unexpected project')
    containers.append({'service': labels.get('com.docker.compose.service'), 'container_id': item['Id'],
                       'image_id': item['Image'], 'running': item['State']['Running']})
root = Path('/opt/srv6-mup-compact/.lab/runtime')
candidate = json.loads((root/'candidate.json').read_text())
builder = json.loads((root/'development/builder.json').read_text()) if (root/'development/builder.json').is_file() else None
images = set(item['image_id'] for item in containers) | {candidate['image_id']}
images.update(candidate.get('nf_images', {}).values())
if builder:
    images.add(builder['image_id'])
print(json.dumps({'containers': containers, 'candidate': candidate, 'builder': builder,
                  'images': json.loads(command('docker', 'image', 'inspect', *sorted(images)))}))
'''


def validate_inventory(data):
    seen, roles = set(), {}
    for item in data['containers']:
        name = item['service']
        if name not in SERVICES or name in seen or item['running'] is not True:
            raise ValueError('missing, stopped, duplicate or unexpected compact service')
        if not re.fullmatch('[0-9a-f]{64}', item['container_id']):
            raise ValueError('invalid container ID')
        seen.add(name)
        roles.setdefault(item['image_id'], []).append(name)
    if seen != SERVICES:
        raise ValueError('incomplete compact service inventory')
    roles.setdefault(data['candidate']['image_id'], []).append('bootstrap')
    for name, identifier in candidate_nf_images(data['candidate']).items():
        roles.setdefault(identifier, []).append('bootstrap:' + name)
    if data['builder']:
        roles.setdefault(data['builder']['image_id'], []).append('builder')
    images = {item['Id']: item for item in data['images']}
    if len(images) != len(data['images']) or set(images) != set(roles):
        raise ValueError('image inventory does not match selected IDs')
    for identifier, image in images.items():
        if not IMAGE_ID.fullmatch(identifier) or type(image.get('Size')) is not int or image['Size'] < 0:
            raise ValueError('invalid immutable image metadata')
        if image.get('Os') != 'linux' or image.get('Architecture') != 'amd64':
            raise ValueError('unsupported image platform')
    return {key: sorted(value) for key, value in sorted(roles.items())}


def inventory(vm):
    vm.owned()
    result = vm.ssh('sudo python3 -c ' + shlex.quote(SNAPSHOT), stdout=subprocess.PIPE, text=True)
    data = json.loads(result.stdout)
    validate_inventory(data)
    return data


def selection(data):
    """Detect lifecycle/image changes during a long scan, excluding volatile stats."""
    return (sorted((x['service'], x['container_id'], x['image_id'], x['running']) for x in data['containers']),
            data['candidate']['image_id'], sorted(candidate_nf_images(data['candidate']).items()),
            (data['builder'] or {}).get('image_id'))


def verify_archive(path, identifier):
    """Bind Docker's config OR OCI manifest ID to verified bytes; never extract."""
    if not IMAGE_ID.fullmatch(identifier):
        raise ValueError('invalid requested image ID')
    with tarfile.open(path, 'r:*') as archive:
        members = {}
        for member in archive:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or str(name) != member.name.rstrip('/')
                    or member.name in members or not (member.isfile() or member.isdir())):
                raise ValueError('unsafe or duplicate image archive member')
            members[member.name] = member
        def read_json(name):
            member = members[name]
            if not member.isfile() or member.size > 4 * 1024**2:
                raise ValueError('invalid image metadata size/type')
            return json.load(archive.extractfile(member))
        def sha(name):
            member = members[name]
            if not member.isfile():
                raise ValueError('expected regular image blob')
            return 'sha256:' + hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest()
        def blob(descriptor):
            value = descriptor['digest']
            if not IMAGE_ID.fullmatch(value):
                raise ValueError('unsupported image blob digest')
            name = 'blobs/sha256/' + value.removeprefix('sha256:')
            if members[name].size != descriptor['size'] or sha(name) != value:
                raise ValueError('image blob size/digest mismatch')
            return name
        saved = read_json('manifest.json')
        if not isinstance(saved, list) or len(saved) != 1:
            raise ValueError('expected exactly one saved image')
        saved = saved[0]
        config_id = sha(saved['Config'])
        config = read_json(saved['Config'])
        if config.get('os') != 'linux' or config.get('architecture') != 'amd64':
            raise ValueError('saved config is not the supported Linux amd64 platform')
        diff_ids = config['rootfs']['diff_ids']
        if not diff_ids or any(not IMAGE_ID.fullmatch(x) for x in diff_ids):
            raise ValueError('invalid image rootfs diff IDs')
        if 'index.json' in members:
            index = read_json('index.json')
            if index.get('schemaVersion') != 2 or len(index['manifests']) != 1 or index['manifests'][0]['digest'] != identifier:
                raise ValueError('saved OCI image does not match selected image ID')
            descriptor = index['manifests'][0]
            kind = 'oci-manifest'
            for _ in range(4):
                manifest = read_json(blob(descriptor))
                if 'manifests' not in manifest:
                    break
                if manifest.get('schemaVersion') != 2:
                    raise ValueError('unsupported OCI index')
                candidates = [x for x in manifest['manifests'] if
                              x.get('platform', {}).get('os') == 'linux' and
                              x.get('platform', {}).get('architecture') == 'amd64']
                if len(candidates) != 1:
                    raise ValueError('missing or ambiguous Linux amd64 platform manifest')
                descriptor = candidates[0]
                kind = 'oci-index'
            else:
                raise ValueError('excessively nested OCI index')
            if manifest.get('schemaVersion') != 2:
                raise ValueError('unsupported OCI manifest')
            if blob(manifest['config']) != saved['Config']:
                raise ValueError('OCI/Docker config mapping mismatch')
            layers = [blob(x) for x in manifest['layers']]
            if layers != saved['Layers'] or len(layers) != len(diff_ids):
                raise ValueError('OCI/Docker layer mapping mismatch')
            layer_digests = [x['digest'] for x in manifest['layers']]
            platform_manifest = descriptor['digest']
        else:
            if config_id != identifier:
                raise ValueError('saved Docker config does not match selected image ID')
            layer_digests = [sha(x) for x in saved['Layers']]
            if layer_digests != diff_ids:
                raise ValueError('Docker layer diff ID mismatch')
            kind = 'docker-config'
            platform_manifest = None
        return {'format': kind, 'selected_image_id': identifier, 'config_id': config_id,
                'platform_manifest_id': platform_manifest, 'layer_digests': layer_digests, 'diff_ids': diff_ids}


def image_policy(report, identifier, diff_ids=None):
    policy = yaml.safe_load((ROOT / 'config/image-distribution-policy.yml').read_text())
    # Recognized licenses produce material requirements, NOT legal approval.
    # Unknown/ambiguous metadata, CVEs and secrets still fail the scanner gate.
    policy['allowed_package_licenses'] = list(license_index(policy))
    evaluator = module('compact_image_policy', ROOT / 'scripts/supply-chain-policy.py')
    supplements = {(x['name'], x['version']): x['licenses'] for x in policy.get('license_metadata', [])}
    failures = evaluator.evaluate(report, policy, supplements)
    if report.get('ArtifactType') != 'container_image' or report.get('Metadata', {}).get('ImageID') != identifier:
        failures.append('scanner image identity does not match the saved immutable image')
    if diff_ids is not None and report.get('Metadata', {}).get('DiffIDs') != diff_ids:
        failures.append('scanner rootfs layers do not match the verified image config')
    packages, secrets, severities = 0, 0, Counter()
    for result in report.get('Results') or []:
        packages += len(result.get('Packages') or [])
        secrets += len(result.get('Secrets') or [])
        severities.update(x.get('Severity', 'UNKNOWN') for x in result.get('Vulnerabilities') or [])
    if secrets:
        failures.append(f'{secrets} secret-pattern findings require review (values not printed)')
    return {'packages': packages, 'vulnerabilities_by_severity': dict(severities),
            'secret_findings': secrets, 'failures': sorted(set(failures)),
            'license_review': classify(report, policy)}


def scanner_environment(cache):
    env = {k: v for k, v in os.environ.items() if not k.startswith('TRIVY_')}
    env.update(TRIVY_CACHE_DIR=str(cache), TRIVY_DISABLE_TELEMETRY='true', GOMAXPROCS='2')
    return env


def image_scan_args(archive, report):
    return ['image', '--input', str(archive), '--scanners', 'vuln,license,secret',
            '--image-config-scanners', 'secret', '--license-full', '--list-all-pkgs',
            '--offline-scan', '--skip-db-update', '--skip-java-db-update',
            '--parallel', '1', '--timeout', '20m', '--ignorefile', '/dev/null',
            '--ignore-unfixed=false', '--format', 'json', '--output', str(report)]


def audit(vm, scan=False):
    snapshot = inventory(vm)
    roles = validate_inventory(snapshot)
    size = sum(x['Size'] for x in snapshot['images'])
    if not scan:
        print(json.dumps({'images': roles, 'estimated_archive_bytes': size,
                          'builder_present': snapshot['builder'] is not None,
                          'publication_approved': False}, indent=2))
        print('Plan only. Save private image archives and run scanners: ./lab audit-images --scan')
        return
    parent = vm.state / 'image-audits'
    if parent.is_symlink():
        raise ValueError('refusing symlink audit directory')
    parent.mkdir(mode=0o700, exist_ok=True)
    parent.chmod(0o700)
    # Include room for Trivy's uncompressed analysis cache; never prune images.
    if shutil.disk_usage(parent).free < size * 2 + 2 * 1024**3:
        raise ValueError('insufficient space for retained image archives and scanner cache')
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    previous_umask = os.umask(0o077)
    manifest = {'schema_version': 1, 'complete': False, 'publication_approved': False,
                'pending': PENDING.copy(), 'images': [], 'selection_unchanged': False,
                'scanner_lock': locks()['verification_tools']['trivy'],
                'policy_sha256': digest(ROOT / 'config/image-distribution-policy.yml'),
                'implementation_sha256': {name: digest(ROOT / name) for name in
                    ('scripts/compact_audit.py', 'scripts/image_distribution.py',
                     'scripts/verification-tool.sh', 'scripts/supply-chain-policy.py')}}
    if snapshot['builder'] is None:
        manifest['pending'].append('builder image absent; builder scan required')
    env = scanner_environment(directory / 'cache')
    def save():
        write(directory / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    def scanner(args, log):
        with log.open('ab') as stream:
            subprocess.run([str(SCANNER), 'trivy', *args], cwd=directory, env=env,
                           stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=1500)
    try:
        save()
        write(directory / 'inventory.json', json.dumps(snapshot, indent=2) + '\n')
        # Empty, private cwd prevents ambient trivy.yaml/.trivyignore/secret config.
        scanner(['image', '--download-db-only', '--no-progress'], directory / 'database.log')
        version = subprocess.check_output([str(SCANNER), 'trivy', '--version', '--format', 'json'],
                                         cwd=directory, env=env, text=True)
        manifest['scanner'] = json.loads(version)
        if manifest['scanner'].get('Version') != manifest['scanner_lock']['version']:
            raise ValueError('scanner version differs from the reviewed lock')
        database = manifest['scanner'].get('VulnerabilityDB', {})
        expiry = datetime.fromisoformat(database.get('NextUpdate', '').replace('Z', '+00:00'))
        if expiry <= datetime.now(timezone.utc):
            raise ValueError('downloaded vulnerability database is already stale')
        db = directory / 'cache/db/trivy.db'
        manifest['database_sha256'] = digest(db)
        save()
        for number, (identifier, names) in enumerate(roles.items(), 1):
            item_dir = directory / identifier.removeprefix('sha256:')
            item_dir.mkdir(mode=0o700)
            item = {'image_id': identifier, 'roles': names, 'complete': False}
            manifest['images'].append(item)
            save()
            print(f'[{number}/{len(roles)}] image audit: {", ".join(names)}', flush=True)
            try:
                archive = item_dir / 'image.tar'
                with archive.open('xb') as stream:
                    vm.ssh('sudo docker image save ' + identifier, stdout=stream)
                item['archive_sha256'] = digest(archive)
                item['verified_identity'] = verify_archive(archive, identifier)
                scanner(image_scan_args(archive, item_dir / 'scan.json'), item_dir / 'scanner.log')
                if digest(archive) != item['archive_sha256']:
                    raise ValueError('image archive changed during scan')
                report = json.loads((item_dir / 'scan.json').read_text())
                proof = item['verified_identity']
                result = image_policy(report, proof['config_id'], proof['diff_ids'])
                write(item_dir / 'policy.json', json.dumps(result, indent=2) + '\n')
                scanner(['convert', '--format', 'cyclonedx', '--output', str(item_dir / 'sbom.cdx.json'),
                         str(item_dir / 'scan.json')], item_dir / 'scanner.log')
                sbom = json.loads((item_dir / 'sbom.cdx.json').read_text())
                if sbom.get('bomFormat') != 'CycloneDX' or not sbom.get('components'):
                    raise ValueError('empty or unsupported generated SBOM')
                item.update(complete=True, policy_findings=len(result['failures']), packages=result['packages'],
                            vulnerabilities_by_severity=result['vulnerabilities_by_severity'],
                            secret_findings=result['secret_findings'],
                            report_sha256=digest(item_dir / 'scan.json'), sbom_sha256=digest(item_dir / 'sbom.cdx.json'))
                print(f'  packages={item["packages"]}, policy findings={item["policy_findings"]}', flush=True)
            except (KeyError, TypeError, ValueError, OSError, tarfile.TarError, subprocess.SubprocessError) as error:
                # Raw diagnostics stay private; never echo potential secret matches.
                item['error'] = type(error).__name__
                write(item_dir / 'error.txt', str(error))
                print(f'  incomplete: {type(error).__name__}; private diagnostics retained', flush=True)
            save()
        final = inventory(vm)
        write(directory / 'inventory-after.json', json.dumps(final, indent=2) + '\n')
        manifest['selection_unchanged'] = selection(snapshot) == selection(final)
        manifest['database_unchanged'] = digest(db) == manifest['database_sha256']
        manifest['complete'] = all(x['complete'] for x in manifest['images']) and manifest['selection_unchanged'] and manifest['database_unchanged']
        manifest['policy_passed'] = manifest['complete'] and not any(x.get('policy_findings', 1) for x in manifest['images'])
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        manifest['error'] = type(error).__name__
        write(directory / 'error.txt', str(error))
    finally:
        save()
        os.umask(previous_umask)
        print(f'Private image evidence: {directory}', flush=True)
    if not manifest.get('policy_passed'):
        raise ValueError('image audit incomplete or policy findings remain; see private manifest/reports')
    print('Scanner policy passed for this snapshot only; M5/publication review remains pending.')
