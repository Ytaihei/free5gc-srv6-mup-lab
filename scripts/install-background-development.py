#!/usr/bin/env python3
"""Preview/install an immutable coordinator; never enable timers or copy auth."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from background_development import ROOT, Store, atomic_json, digest, load_policy, safe_relative
from background_executor import ACCOUNTS, HOMES, INSTALL, REVIEW_SCHEMA, STATE, protected

UNITS = ('srv6-mup-background.service', 'srv6-mup-background.timer',
         'srv6-mup-background-watchdog.service', 'srv6-mup-background-watchdog.timer')
UNIT_DIRECTORY = Path('/etc/systemd/system')
COORDINATOR_UPDATE_PATHS = {
    'scripts/background-admin.py', 'scripts/background_executor.py',
    'scripts/install-background-development.py', 'tests/test_background_development.py',
    'docs/background-development.md', 'docs/background-development.ja.md',
    'config/documentation.json',
}


def update_coordinator():
    """Explicit, narrow repair of a stopped installation; preserve auth and data."""
    protected(INSTALL / 'installation.json')
    protected(INSTALL / 'operator.json')
    manifest = json.loads((INSTALL / 'installation.json').read_text())
    inventory = json.loads((ROOT / 'config/public-source.json').read_text())['files']
    if set(inventory) | {'bin/codex', 'review-schema.json'} != set(manifest['files']):
        raise ValueError('coordinator repair cannot add/remove inventory or tools')
    for name, expected in manifest['files'].items():
        safe_relative(name)
        protected(INSTALL / name)
        if digest(INSTALL / name) != expected:
            raise ValueError('installed files differ from their manifest; preserve and inspect them')
    changes = {}
    for name in inventory:
        safe_relative(name)
        source = ROOT / name
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(ROOT):
            raise ValueError('unsafe source for coordinator repair')
        payload = source.read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest['files'][name]:
            if name not in COORDINATOR_UPDATE_PATHS:
                raise ValueError('coordinator repair cannot change policy, dependencies, units or lab code')
            changes[name] = payload
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
        if not changes:
            print('Coordinator source already matches; no files or state changed.')
            return
        parent = STATE / 'updates'
        parent.mkdir(mode=0o700, exist_ok=True)
        backup = parent / uuid.uuid4().hex
        backup.mkdir(mode=0o700)
        atomic_json(backup / 'installation.json', manifest)
        operator = json.loads((INSTALL / 'operator.json').read_text())
        atomic_json(backup / 'operator.json', operator)
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
    parser.add_argument('--go-root', type=Path, help='existing pinned Go installation directory')
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
        update_coordinator()
        return
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
    go_root = args.go_root.resolve(strict=True)
    if not stat.S_ISREG(codex.stat().st_mode) or not (go_root / 'bin/go').is_file():
        raise ValueError('invalid tool installation')
    # Metadata checks do not install/update Codex or select a new model.
    codex_version = subprocess.check_output([codex, '--version'], text=True).strip()
    locked_go = __import__('yaml').safe_load((ROOT / 'config/versions.lock.yml').read_text())['toolchains']['go']['version']
    go_version = subprocess.check_output([go_root / 'bin/go', 'version'], text=True).strip()
    if go_version != f'go version go{locked_go} linux/amd64':
        raise ValueError('Go installation differs from the reviewed lock')
    if codex_version != 'codex-cli 0.154.0':
        raise ValueError('this coordinator requires a reviewed Codex CLI 0.154.0 executable')
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
    shutil.copyfile(codex, INSTALL / 'bin/codex')
    (INSTALL / 'bin/codex').chmod(0o755)
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
    for name in ('bin/codex', 'review-schema.json'):
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
