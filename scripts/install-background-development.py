#!/usr/bin/env python3
"""Preview/install an immutable coordinator; never enable timers or copy auth."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid

from background_development import ROOT, Store, atomic_json, digest, load_policy, safe_relative
from background_executor import ACCOUNTS, CODEX_FILES, HOMES, INSTALL, REVIEW_SCHEMA, STATE, protected

UNITS = ('srv6-mup-background.service', 'srv6-mup-background.timer',
         'srv6-mup-background-watchdog.service', 'srv6-mup-background-watchdog.timer')
UNIT_DIRECTORY = Path('/etc/systemd/system')
COORDINATOR_UPDATE_PATHS = {
    'scripts/background-admin.py', 'scripts/background_executor.py',
    'scripts/install-background-development.py', 'tests/test_background_development.py',
    'docs/background-development.md', 'docs/background-development.ja.md',
    'config/documentation.json',
}
# Deliberately one reviewed security transition, not a general dependency updater.
GO_REPAIR_VERSION = '1.26.9'
GO_REPAIR_SHA256 = '42d158b4d8f7b61ac0a830567c940a86098fb7aac52e467a5ebec03ef5cc2f8d'
GO_PREVIOUS_SHA256 = 'd0f743b33e8d8945e6b1f432edd15785c70507121d6e2a723b21285eddf8b57b'
GO_REPAIR_PATHS = {
    'go.mod', 'config/versions.lock.yml', 'ansible/inventory/group_vars/all.yml',
    'config/supply-chain-policy.yml', 'config/image-distribution-policy.yml',
    'config/public-test-keys.json', 'docs/image-distribution.md', 'docs/image-distribution.ja.md',
}


def go_repair_bytes(payload):
    return payload.replace(b'1.26.8', GO_REPAIR_VERSION.encode()).replace(
        GO_PREVIOUS_SHA256.encode(), GO_REPAIR_SHA256.encode())


def go_archive_payload(path):
    """Verify the exact upstream archive before parsing or executing any bytes."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 100_000_000:
            raise ValueError('Go archive must be a bounded regular file')
        payload = stream.read(100_000_001)
    if hashlib.sha256(payload).hexdigest() != GO_REPAIR_SHA256:
        raise ValueError('Go archive differs from the reviewed upstream SHA-256')
    return payload


def stage_go_archive(payload, destination):
    """Extract only plain bounded members into a new private staging directory."""
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        members, names, total = [], set(), 0
        for member in archive:
            name = member.name.rstrip('/')
            safe_relative(name)
            if (name != 'go' and not name.startswith('go/')) or name in names:
                raise ValueError('unexpected or duplicate Go archive path')
            if not (member.isdir() or member.isreg()) or member.mode & 0o7000:
                raise ValueError('Go archive links and special files are refused')
            names.add(name)
            total += member.size
            if total > 600_000_000 or len(names) > 50_000:
                raise ValueError('Go archive exceeds extraction bounds')
            members.append(member)
        destination.mkdir(mode=0o700)
        archive.extractall(destination, members=members, filter='data')
    sdk = destination / 'go'
    if (sdk / 'VERSION').read_text().splitlines()[0] != 'go' + GO_REPAIR_VERSION:
        raise ValueError('Go archive version differs from the reviewed version')
    for entry in (sdk, *sdk.rglob('*')):
        entry.chmod(0o755 if entry.is_dir() or entry.stat().st_mode & 0o111 else 0o644)
    return sdk


def check_go_repair_installation():
    sdk = INSTALL / 'go'
    protected(sdk)
    if not sdk.is_dir() or sdk.is_symlink():
        raise ValueError('installed Go SDK must be a protected directory')
    for entry in sdk.rglob('*'):
        protected(entry)
        if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
            raise ValueError('installed Go SDK contains links or special files')
    version = (sdk / 'VERSION').read_text().splitlines()[0]
    if version not in ('go1.26.8', 'go' + GO_REPAIR_VERSION):
        raise ValueError('only the reviewed Go 1.26.8 to 1.26.9 repair is supported')
    link = INSTALL / 'bin/go'
    protected(link.parent)
    if not link.is_symlink() or os.readlink(link) != '../go/bin/go':
        raise ValueError('installed Go launcher must retain its expected SDK link')
    if sdk.stat().st_dev != STATE.stat().st_dev:
        raise ValueError('Go SDK and private backup must be on the same filesystem')


def codex_package_payloads(directory):
    """Copy only reviewed bytes, not arbitrary executables from a package tree."""
    directory = Path(directory).resolve(strict=True)
    payloads = {}
    for name, expected in CODEX_FILES.items():
        path = directory / name
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.resolve() != path):
            raise ValueError('Codex package entries must be plain files without links')
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError('Codex package differs from the reviewed 0.154.0 Linux x86-64 bytes')
        payloads[name] = payload
    return payloads


def update_coordinator(codex_package=None, go_archive=None):
    """Explicit, narrow repair of a stopped installation; preserve auth and data."""
    protected(INSTALL / 'installation.json')
    protected(INSTALL / 'operator.json')
    manifest = json.loads((INSTALL / 'installation.json').read_text())
    inventory = json.loads((ROOT / 'config/public-source.json').read_text())['files']
    legacy = set(inventory) | {'bin/codex', 'review-schema.json'}
    if set(manifest['files']) not in (legacy, legacy | set(CODEX_FILES)):
        raise ValueError('coordinator repair cannot add/remove inventory or tools')
    for name, expected in manifest['files'].items():
        safe_relative(name)
        protected(INSTALL / name)
        if digest(INSTALL / name) != expected:
            raise ValueError('installed files differ from their manifest; preserve and inspect them')
    go_payload = None
    if go_archive is not None:
        go_payload = go_archive_payload(go_archive)
        check_go_repair_installation()
        if not GO_REPAIR_PATHS.issubset(inventory):
            raise ValueError('Go repair requires the complete reviewed source inventory')
    changes = {}
    for name in inventory:
        safe_relative(name)
        source = ROOT / name
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(ROOT):
            raise ValueError('unsafe source for coordinator repair')
        payload = source.read_bytes()
        if go_payload is not None and name in GO_REPAIR_PATHS:
            if payload != go_repair_bytes((INSTALL / name).read_bytes()):
                raise ValueError('Go repair accepts only the exact reviewed pin substitutions')
            if name == 'config/versions.lock.yml':
                locked = __import__('yaml').safe_load(payload)['toolchains']['go']
                if (locked['version'] != GO_REPAIR_VERSION or locked['sha256'] != GO_REPAIR_SHA256
                        or locked['url'] != f'https://go.dev/dl/go{GO_REPAIR_VERSION}.linux-amd64.tar.gz'
                        or locked['platform'] != 'linux-amd64'):
                    raise ValueError('Go repair candidate does not pin the reviewed SDK')
        if hashlib.sha256(payload).hexdigest() != manifest['files'][name]:
            if name not in COORDINATOR_UPDATE_PATHS and not (go_payload is not None and name in GO_REPAIR_PATHS):
                raise ValueError('coordinator repair cannot change policy, dependencies, units or lab code')
            changes[name] = payload
    additions = {}
    if codex_package is not None:
        payloads = codex_package_payloads(codex_package)
        if manifest['files']['bin/codex'] != CODEX_FILES['bin/codex']:
            raise ValueError('package repair cannot replace the installed Codex executable')
        for name, payload in payloads.items():
            if name in manifest['files']:
                if manifest['files'][name] != CODEX_FILES[name]:
                    raise ValueError('package repair cannot replace installed tool bytes')
                continue
            target = INSTALL / name
            if target.exists() or target.is_symlink():
                raise ValueError('untracked package entry retained; inspect partial repair')
            for parent_path in target.parents:
                if parent_path.exists() or parent_path.is_symlink():
                    protected(parent_path)
                if parent_path == INSTALL:
                    break
            additions[name] = payload
    for name in (*UNITS, 'srv6-mup-background-test.service'):
        active = subprocess.check_output(
            ['systemctl', 'show', name, '--property=ActiveState', '--value'], text=True).strip()
        if active not in ('inactive', 'failed'):
            raise ValueError('stop background services and timers before coordinator repair')
        if name.endswith('.timer'):
            enabled = subprocess.check_output(
                ['systemctl', 'show', name, '--property=UnitFileState', '--value'], text=True).strip()
            if enabled != 'disabled':
                raise ValueError('disable both timers before coordinator repair')
    store = Store(STATE)
    with store.locked():
        state = store.read()
        if state.get('active'):
            raise ValueError('recover interrupted work before coordinator repair')
        if not changes and not additions and go_payload is None:
            print('Coordinator source already matches; no files or state changed.')
            return
        parent = STATE / 'updates'
        parent.mkdir(mode=0o700, exist_ok=True)
        backup = parent / uuid.uuid4().hex
        backup.mkdir(mode=0o700)
        atomic_json(backup / 'installation.json', manifest)
        operator = json.loads((INSTALL / 'operator.json').read_text())
        atomic_json(backup / 'operator.json', operator)
        atomic_json(backup / 'added-files.json', {name: CODEX_FILES[name] for name in additions})
        for name in changes:
            target = backup / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes((INSTALL / name).read_bytes())
            target.chmod(0o600)
        state['paused'] = True
        store.save(state)
        operator.update({'commissioned': False, 'automatic_merge': False})
        atomic_json(INSTALL / 'operator.json', operator)
        print('Repair backup retained at ' + str(backup), flush=True)
        if go_payload is not None:
            staged = stage_go_archive(go_payload, backup / 'go-staging')
            # Same-filesystem renames retain the old SDK. Failure stays paused;
            # do not automatically resume or discard either recovery tree.
            os.rename(INSTALL / 'go', backup / 'go-previous')
            os.rename(staged, INSTALL / 'go')
            manifest['go_archive'] = {'version': GO_REPAIR_VERSION, 'sha256': GO_REPAIR_SHA256}
        for name, payload in changes.items():
            target = INSTALL / name
            descriptor, temporary = tempfile.mkstemp(prefix='.coordinator-update-', dir=target.parent)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), target.stat().st_mode & 0o777)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            manifest['files'][name] = hashlib.sha256(payload).hexdigest()
        for name, payload in additions.items():
            target = INSTALL / name
            directory = INSTALL
            for part in Path(name).parts[:-1]:
                directory /= part
                if not directory.exists():
                    directory.mkdir(mode=0o755)
                    directory.chmod(0o755)
                protected(directory)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o644 if name == 'codex-package.json' else 0o755)
            manifest['files'][name] = CODEX_FILES[name]
        # A partial write keeps the old manifest and fails verification on resume.
        atomic_json(INSTALL / 'installation.json', manifest)
        print('Coordinator repaired. Authentication/state/evidence retained; recommission before resume.')


def check_units(adopt=False):
    """Check conflicts before creating packages, accounts or installation state."""
    existing = []
    for name in UNITS:
        target = UNIT_DIRECTORY / name
        if not target.exists() and not target.is_symlink():
            continue
        if not adopt:
            raise ValueError('existing systemd unit is preserved; review --adopt-existing-units')
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_nlink != 1
                or target.read_bytes() != (ROOT / 'configs/systemd' / name).read_bytes()):
            raise ValueError('existing systemd unit is not an exact protected template')
        existing.append(name)
    # An override can change the behavior of an otherwise matching template.
    for name in UNITS:
        fragment = subprocess.check_output(
            ['systemctl', 'show', name, '--property=FragmentPath', '--value'], text=True).strip()
        if fragment and fragment != str(UNIT_DIRECTORY / name):
            raise ValueError('a systemd unit outside the installation directory already exists')
        overrides = subprocess.check_output(
            ['systemctl', 'show', name, '--property=DropInPaths', '--value'], text=True)
        if overrides.strip():
            raise ValueError('systemd drop-ins require separate operator review')
        if name.endswith('.service'):
            active = subprocess.check_output(
                ['systemctl', 'show', name, '--property=ActiveState', '--value'], text=True).strip()
            if active not in ('inactive', 'failed'):
                raise ValueError('background service must be inactive before installation')
    return existing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--codex', type=Path, help='existing, trusted standalone Codex executable')
    parser.add_argument('--codex-package', type=Path,
                        help='explicitly restore missing companions from the pinned package during coordinator repair')
    parser.add_argument('--go-root', type=Path, help='existing pinned Go installation directory')
    parser.add_argument('--go-archive', type=Path,
                        help='explicit Go 1.26.8 to 1.26.9 security repair from the reviewed upstream archive')
    parser.add_argument('--adopt-existing-units', action='store_true',
                        help='reuse exact protected templates after disabling their timers; never overwrite custom units')
    parser.add_argument('--update-coordinator', action='store_true',
                        help='repair only reviewed coordinator source; preserve accounts, credentials, tools and lab data')
    args = parser.parse_args()
    load_policy()
    if args.update_coordinator:
        if args.codex or args.go_root or args.adopt_existing_units:
            raise ValueError('coordinator repair cannot be combined with installation options')
        if not args.apply:
            print('Preview: repair reviewed coordinator source with a private backup, then require recommissioning.')
            return
        if os.geteuid() != 0:
            raise ValueError('--apply requires sudo; no password is read by this script')
        update_coordinator(args.codex_package, args.go_archive)
        return
    if args.go_archive:
        raise ValueError('--go-archive is only for --update-coordinator')
    if args.codex_package:
        raise ValueError('--codex-package is only for --update-coordinator; fresh installation uses --codex')
    print('Install fixed coordinator under /opt/srv6-mup-background; private state under /var/lib.')
    print('Create isolated mup-bg-worker/checks/publisher accounts, without administrative groups.')
    print('Install git, curl, make, jq, gh, Python venv; create pinned Python environment.')
    print('Install disabled systemd timers: Mon/Wed/Fri 03:00 Asia/Tokyo; no missed-run catch-up.')
    print('NO authentication copying, timer enablement, repository publication or lab operation.')
    if not args.apply:
        return
    if os.geteuid() != 0:
        raise ValueError('--apply requires sudo; no password is read by this script')
    if INSTALL.exists() or INSTALL.is_symlink() or STATE.exists() or STATE.is_symlink():
        raise ValueError('existing installation/state is preserved; upgrades need separate review')
    existing_units = check_units(args.adopt_existing_units)
    if not args.codex or not args.go_root:
        raise ValueError('--codex and --go-root must name already trusted tool installations')
    codex = args.codex.resolve(strict=True)
    if codex.name != 'codex' or codex.parent.name != 'bin':
        raise ValueError('--codex must name bin/codex in the complete pinned standalone package')
    package = codex_package_payloads(codex.parent.parent)
    go_root = args.go_root.resolve(strict=True)
    if not stat.S_ISREG(codex.stat().st_mode) or not (go_root / 'bin/go').is_file():
        raise ValueError('invalid tool installation')
    # Metadata checks do not install/update Codex or select a new model.
    # Package bytes already prove this version; never execute an operator's
    # mutable source path with root privileges just to obtain a version string.
    codex_version = 'codex-cli 0.154.0'
    locked_go = __import__('yaml').safe_load((ROOT / 'config/versions.lock.yml').read_text())['toolchains']['go']['version']
    go_version = subprocess.check_output([go_root / 'bin/go', 'version'], text=True).strip()
    if go_version != f'go version go{locked_go} linux/amd64':
        raise ValueError('Go installation differs from the reviewed lock')
    inventory = json.loads((ROOT / 'config/public-source.json').read_text())['files']
    for name in inventory:
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError('source inventory contains an unsafe entry')
    for account in ACCOUNTS.values():
        try:
            pwd.getpwnam(account)
        except KeyError:
            pass
        else:
            raise ValueError('reserved account already exists; do not adopt unrelated accounts')
    # All conflicts, tools, source files and account names were checked first.
    for name in existing_units:
        if name.endswith('.timer'):
            subprocess.run(['systemctl', 'disable', '--now', name], check=True)
    # Shared source/tools are public, while account homes and state explicitly
    # retain 0700/0600. Do not inherit a caller's restrictive umask for the venv.
    os.umask(0o022)
    subprocess.run(['apt-get', 'update'], check=True)
    subprocess.run(['apt-get', 'install', '-y', 'git', 'curl', 'make', 'jq', 'gh',
                    'python3-venv', 'python3-yaml', 'ca-certificates'], check=True)
    INSTALL.mkdir(mode=0o755)
    STATE.mkdir(mode=0o700)
    # Partial installs are deliberately retained and never overwritten on retry.
    for name in inventory:
        source, target = ROOT / name, INSTALL / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copyfile(source, target)
        target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
    for role, account in ACCOUNTS.items():
        subprocess.run(['useradd', '--system', '--user-group', '--create-home',
                        '--home-dir', str(HOMES[role]), '--shell', '/usr/sbin/nologin', account], check=True)
        HOMES[role].chmod(0o700)
    (INSTALL / 'bin').mkdir(mode=0o755)
    for name, payload in package.items():
        target = INSTALL / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        target.write_bytes(payload)
        target.chmod(0o644 if name == 'codex-package.json' else 0o755)
    shutil.copytree(go_root, INSTALL / 'go', symlinks=True)
    (INSTALL / 'bin/go').symlink_to('../go/bin/go')
    subprocess.run(['python3', '-m', 'venv', str(INSTALL / 'venv')], check=True)
    subprocess.run([str(INSTALL / 'venv/bin/pip'), 'install', '--disable-pip-version-check',
                    '-r', str(INSTALL / 'requirements-dev.txt')], check=True)
    (INSTALL / 'candidates').mkdir(mode=0o755)
    (INSTALL / 'review-schema.json').write_text(json.dumps(REVIEW_SCHEMA, indent=2) + '\n')
    # Operator settings are private, never included in an exported source tree.
    atomic_json(INSTALL / 'operator.json', {
        'version': 1, 'model': 'gpt-6-astra', 'codex_version': codex_version,
        'author_name': '', 'author_email': '', 'commissioned': False,
        'automatic_merge': False, 'lab_rehearsals': {'compact': False, 'reference': False}})
    files = {name: hashlib.sha256((INSTALL / name).read_bytes()).hexdigest() for name in inventory}
    for name in (*CODEX_FILES, 'review-schema.json'):
        files[name] = hashlib.sha256((INSTALL / name).read_bytes()).hexdigest()
    atomic_json(INSTALL / 'installation.json', {'version': 1, 'files': files})
    for name in UNITS:
        target = UNIT_DIRECTORY / name
        if name in existing_units:
            # Preserve matching units; do not overwrite concurrent changes.
            if target.is_symlink() or target.read_bytes() != (INSTALL / 'configs/systemd' / name).read_bytes():
                raise ValueError('existing systemd unit changed during installation')
            continue
        if target.exists() or target.is_symlink():
            raise ValueError('existing systemd unit is preserved')
        shutil.copyfile(INSTALL / 'configs/systemd' / name, target)
        target.chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('Installed but PAUSED. Complete account login, isolation rehearsal and explicit commissioning.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit('Installation stopped; retained any partial state: ' + str(error))
