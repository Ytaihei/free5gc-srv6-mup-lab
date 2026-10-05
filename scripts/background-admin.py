#!/usr/bin/env python3
"""Explicit commissioning and interruption acknowledgement; never a lab shell."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from background_development import Store, atomic_json
from background_executor import INSTALL, ROOT, STATE, protected, service, watchdog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('watchdog')
    commission = sub.add_parser('commission')
    commission.add_argument('--author-name', required=True)
    commission.add_argument('--author-email', required=True)
    acknowledge = sub.add_parser('acknowledge')
    acknowledge.add_argument('--run', required=True, help='exact interrupted development run ID')
    args = parser.parse_args()
    if ROOT != INSTALL or os.geteuid() != 0:
        raise ValueError('only the root-owned installed administration tool can perform this operation')
    protected(INSTALL / 'installation.json')
    from background_development import digest
    for name, expected in json.loads((INSTALL / 'installation.json').read_text())['files'].items():
        protected(INSTALL / name)
        if digest(INSTALL / name) != expected:
            raise ValueError('installation differs from the reviewed bytes')
    store = Store(STATE)
    if args.action == 'watchdog':
        try:
            watchdog(store)
        except ValueError as error:
            if str(error) != 'another background operation is running':
                raise
        return
    with store.locked():
        state = store.read()
        if args.action == 'acknowledge':
            active = state.get('active')
            if not active or active.get('id') != args.run or active.get('kind') != 'development':
                raise ValueError('only an exact interrupted development run can be acknowledged')
            if subprocess.run(['systemctl', 'is-active', '--quiet', 'srv6-mup-worker-' + args.run]).returncode == 0:
                raise ValueError('worker is still active')
            state.setdefault('acknowledged_runs', []).append(active)
            state['active'] = None
            state['paused'] = True
            state['failures'] = 0
            store.save(state)
            print('Acknowledged; source and evidence retained, scheduler still paused.')
            return
        if state.get('active'):
            raise ValueError('recover interrupted work before commissioning')
        for value in (args.author_name, args.author_email):
            if not value.strip() or '\n' in value:
                raise ValueError('invalid explicit commit identity')
        # Check commands have no repository content and no credential values in
        # stdout. No ChatGPT auth is copied into the publisher or test account.
        service('worker', [INSTALL / 'bin/codex', 'login', 'status'], seconds=30)
        service('publisher', ['gh', 'auth', 'status'], seconds=30)
        probe = (
            'import os,pathlib,socket,errno; '
            'assert os.getuid()!=0; '
            'assert not os.access("/var/lib/mup-bg-worker/.codex",os.R_OK); '
            'assert not os.access("/var/lib/mup-bg-publisher/.config/gh",os.R_OK); '
            'assert not os.access("/home",os.W_OK); '
            'assert not pathlib.Path("/run/docker.sock").exists(); '
            'assert not pathlib.Path("/run/libvirt").exists(); '
            's=socket.socket(); s.settimeout(2); '
            'assert s.connect_ex(("127.0.0.2",9)) in (errno.EPERM,errno.EACCES)')
        service('checks', ['python3', '-c', probe], seconds=30)
        service('checks', ['make', 'check'], cwd=INSTALL, seconds=900)
        operator = json.loads((INSTALL / 'operator.json').read_text())
        operator.update({'author_name': args.author_name, 'author_email': args.author_email,
                         'commissioned': True, 'automatic_merge': False})
        atomic_json(INSTALL / 'operator.json', operator)
        print('Code-only isolation/auth checks passed. Still paused; auto-merge and live lab operations remain off.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit('Administration refused: ' + str(error))
