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

from background_development import ROOT, atomic_json, load_policy
from background_executor import ACCOUNTS, HOMES, INSTALL, REVIEW_SCHEMA, STATE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--codex', type=Path, help='existing, trusted standalone Codex executable')
    parser.add_argument('--go-root', type=Path, help='existing pinned Go installation directory')
    args = parser.parse_args()
    load_policy()
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
    for name in ('srv6-mup-background.service', 'srv6-mup-background.timer',
                 'srv6-mup-background-watchdog.service', 'srv6-mup-background-watchdog.timer'):
        target = Path('/etc/systemd/system') / name
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
