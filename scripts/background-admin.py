#!/usr/bin/env python3
"""Explicit commissioning and interruption acknowledgement; never a lab shell."""
import argparse
import errno
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import uuid

from background_development import Store, atomic_json
from background_executor import INSTALL, ROOT, STATE, protected, service, watchdog


def directory_denied(path):
    """InaccessiblePaths masks the inode; it need not disappear from stat()."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as error:
        return error.errno in (errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR)
    os.close(descriptor)
    return False


def socket_denied(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        try:
            result = connection.connect_ex(path)
        except OSError as error:
            result = error.errno
    # A stopped but reachable daemon (ECONNREFUSED) is not proof of isolation.
    return result in (errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR)


def isolation_probe():
    checks = {
        'unprivileged': os.getuid() != 0,
        'worker_credentials_denied': not os.access('/var/lib/mup-bg-worker/.codex', os.R_OK),
        'publisher_credentials_denied': not os.access('/var/lib/mup-bg-publisher/.config/gh', os.R_OK),
        'host_home_not_writable': not os.access('/home', os.W_OK),
        'libvirt_directory_denied': directory_denied('/run/libvirt'),
        'docker_socket_denied': socket_denied('/run/docker.sock'),
        'libvirt_socket_denied': socket_denied('/run/libvirt/libvirt-sock'),
    }
    with socket.socket() as connection:
        connection.settimeout(2)
        try:
            network_errno = connection.connect_ex(('127.0.0.2', 9))
        except OSError as error:
            network_errno = error.errno
    checks['private_network_denied'] = network_errno in (errno.EACCES, errno.EPERM)
    # Fixed labels/booleans only: safe to inspect without disclosing credentials.
    print(json.dumps({'checks': checks, 'network_errno': network_errno}), flush=True)
    if not all(checks.values()):
        raise ValueError('isolation probe failed: ' + ', '.join(key for key, passed in checks.items() if not passed))


def commission(store, state, author_name, author_email):
    for value in (author_name, author_email):
        if not value.strip() or '\n' in value:
            raise ValueError('invalid explicit commit identity')
    operator = json.loads((INSTALL / 'operator.json').read_text())
    # A failed recommission must not leave an earlier acceptance active.
    state['paused'] = True
    store.save(state)
    operator.update({'commissioned': False, 'automatic_merge': False})
    atomic_json(INSTALL / 'operator.json', operator)
    parent = STATE / 'commissioning'
    parent.mkdir(mode=0o700, exist_ok=True)
    evidence = parent / uuid.uuid4().hex
    evidence.mkdir(mode=0o700)
    phases = (
        ('worker-auth', 'worker', [INSTALL / 'bin/codex', 'login', 'status'], 30),
        ('publisher-auth', 'publisher', ['gh', 'auth', 'status'], 30),
        ('isolation-probe', 'checks', ['python3', INSTALL / 'scripts/background-admin.py', 'probe-isolation'], 30),
        ('source-checks', 'checks', ['make', 'check'], 900),
    )
    result = {'complete': False, 'phases': {}}
    for name, role, command, seconds in phases:
        result['phases'][name] = 'running'
        atomic_json(evidence / 'result.json', result)
        print('Commissioning: ' + name, flush=True)
        try:
            service(role, command, cwd=INSTALL, seconds=seconds, output_file=evidence / (name + '.log'))
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            result['phases'][name] = 'failed'
            atomic_json(evidence / 'result.json', result)
            raise ValueError(f'{name} failed; inspect private log {evidence / (name + ".log")}') from error
        result['phases'][name] = 'passed'
        atomic_json(evidence / 'result.json', result)
    operator.update({'author_name': author_name, 'author_email': author_email, 'commissioned': True})
    atomic_json(INSTALL / 'operator.json', operator)
    result['complete'] = True
    atomic_json(evidence / 'result.json', result)
    print('Code-only isolation/auth checks passed. Still paused; auto-merge and live lab operations remain off.')
    print('Private commissioning evidence: ' + str(evidence))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('watchdog')
    sub.add_parser('probe-isolation', help='read-only probe for the isolated checks account')
    commission_parser = sub.add_parser('commission')
    commission_parser.add_argument('--author-name', required=True)
    commission_parser.add_argument('--author-email', required=True)
    acknowledge = sub.add_parser('acknowledge')
    acknowledge.add_argument('--run', required=True, help='exact interrupted development run ID')
    args = parser.parse_args()
    if args.action == 'probe-isolation':
        isolation_probe()
        return
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
        commission(store, state, args.author_name, args.author_email)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit('Administration refused: ' + str(error))
