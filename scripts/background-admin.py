#!/usr/bin/env python3
"""Explicit commissioning and interruption acknowledgement; never a lab shell."""
import argparse
import errno
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import socket
import stat
import sys
import tempfile
import uuid

from background_development import Store, atomic_json, digest, load_policy, safe_relative
from background_executor import (ACCOUNTS, INSTALL, ROOT, STATE, protected, service,
                                 watchdog, worker_work_parent, launch_test_once, run, verify_codex_bundle,
                                 diagnostic_hint, emit_diagnostic, private_diagnostic_text,
                                 read_diagnostics, write_diagnostic, diagnostic_record,
                                 LOG_GROUP, AUTH_PHASES, operator_log_directory,
                                 write_operator_event, read_operator_logs)


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


def private_network_errno():
    """Observe a packet-filter denial directly, without TCP's SYN retries."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(2)
        # Own the destination port: a failed filter must not contact a host daemon.
        # Setup failures are not evidence of an egress denial.
        connection.bind(('127.0.0.2', 0))
        destination = connection.getsockname()
        try:
            connection.sendto(b'srv6-mup-isolation-probe', destination)
        except OSError as error:
            return error.errno
    return 0


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
    network_errno = private_network_errno()
    checks['private_network_denied'] = network_errno in (errno.EACCES, errno.EPERM)
    # Fixed labels/booleans only: safe to inspect without disclosing credentials.
    print(json.dumps({'checks': checks, 'network_probe': 'udp-self-send',
                      'network_errno': network_errno}), flush=True)
    if not all(checks.values()):
        raise ValueError('isolation probe failed: ' + ', '.join(key for key, passed in checks.items() if not passed))


def commissioning_source(destination):
    """Freeze only verified public files, never installed tools/private state."""
    protected(INSTALL / 'installation.json')
    protected(INSTALL / 'config/public-source.json')
    protected(destination.parent)
    manifest = json.loads((INSTALL / 'installation.json').read_text())['files']
    if digest(INSTALL / 'config/public-source.json') != manifest['config/public-source.json']:
        raise ValueError('installed publication inventory changed')
    inventory = json.loads((INSTALL / 'config/public-source.json').read_text())['files']
    destination.mkdir(mode=0o755)  # Refuse reuse; keep failed snapshots as evidence.
    destination.chmod(0o755)
    for name in inventory:
        relative = safe_relative(name)
        source = INSTALL / name
        protected(source)
        directory = destination
        for part in relative.parts[:-1]:
            directory /= part
            directory.mkdir(mode=0o755, exist_ok=True)
            directory.chmod(0o755)
        target = destination / name
        shutil.copyfile(source, target)
        target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
        if digest(target) != manifest.get(name):
            raise ValueError('commissioning source differs from installed manifest')


def commissioning_workspace(run_id):
    parent = worker_work_parent()
    name = 'commission-' + uuid.UUID(hex=run_id).hex
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    parent_fd = os.open(parent, flags)
    try:
        info = os.fstat(parent_fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o7022:
            raise ValueError('unexpected worker work parent; preserved')
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        try:
            worker = pwd.getpwnam(ACCOUNTS['worker'])
            os.fchown(descriptor, worker.pw_uid, worker.pw_gid)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_fd)
    return parent / name


def workspace_probe():
    checks = {'unprivileged': os.geteuid() != 0,
              'parent_searchable': os.access('..', os.X_OK),
              'parent_not_writable': not os.access('..', os.W_OK),
              'workspace_writable': os.access('.', os.W_OK | os.X_OK)}
    with tempfile.TemporaryFile(dir='.') as probe:
        probe.write(b'workspace-probe')
        probe.flush()
        probe.seek(0)
        checks['workspace_io'] = probe.read() == b'workspace-probe'
    print(json.dumps({'checks': checks}), flush=True)
    if not all(checks.values()):
        raise ValueError('worker workspace probe failed: ' + ', '.join(
            key for key, passed in checks.items() if not passed))


def sandbox_probe():
    """Run the pinned Linux helper, without config, credentials or a model."""
    if os.geteuid() == 0:
        raise ValueError('sandbox probe must run unprivileged')
    workspace = str(Path.cwd())
    profile = {
        'type': 'managed',
        'file_system': {'type': 'restricted', 'entries': [
            {'path': {'type': 'special', 'value': {'kind': 'root'}}, 'access': 'read'},
            {'path': {'type': 'path', 'path': workspace}, 'access': 'write'},
        ]},
        'network': 'restricted',
    }
    # argv[0] selects the internal helper in the hash-pinned 0.154.0 binary.
    # Let that helper handle a denied fresh proc mount just as it does during
    # model execution. Never substitute a bare command or disable sandboxing.
    os.execv(str(INSTALL / 'bin/codex'), [
        'codex-linux-sandbox', '--sandbox-policy-cwd', workspace,
        '--command-cwd', workspace, '--permission-profile', json.dumps(profile),
        '--', '/bin/true',
    ])


def commissioning_tools(workspace, evidence, model):
    """Exercise the actual configured model/tool path, not just --version/login."""
    verify_codex_bundle()
    # Fixed, model-free sandbox smoke test in the actual worker service. Use
    # the same PATH preference as the worker, with its pinned helper fallback.
    path = str(INSTALL / 'venv/bin') + ':' + str(INSTALL / 'bin') + ':/usr/bin:/bin'
    bwrap = Path(shutil.which('bwrap', path=path) or INSTALL / 'codex-resources/bwrap').resolve()
    protected(bwrap, executable=True)
    service('worker', [INSTALL / 'venv/bin/python3', INSTALL / 'scripts/background-admin.py',
                       'probe-sandbox'],
            cwd=workspace, seconds=30, output_file=evidence / 'worker-sandbox.log')
    challenge = uuid.uuid4().hex
    probe = workspace / 'tool-probe.txt'
    prompt = (
        'Commissioning probe only. Use the execution tool to run /usr/bin/python3 -c '
        + json.dumps("from pathlib import Path; Path('tool-probe.txt').write_text(" + repr(challenge) + ")")
        + '. Do not use apply_patch, inspect credentials, access the network, change other files or run tests. '
        'Then state whether the command succeeded. Do not pretend success if tools are unavailable.')
    log = evidence / 'worker-tools.jsonl'
    service('worker', [INSTALL / 'bin/codex', 'exec', '--ignore-user-config', '--ignore-rules',
                      '--skip-git-repo-check', '--sandbox', 'workspace-write', '-c', 'approval_policy="never"',
                      '--model', model, '--json', '-'], cwd=workspace, seconds=180,
            input_text=prompt, output_file=log, json_events=True)
    descriptor = os.open(probe, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(challenge)
                or info.st_uid != pwd.getpwnam(ACCOUNTS['worker']).pw_uid or stream.read(128) != challenge.encode()):
            raise ValueError('worker tool probe did not create the expected private artifact')


def retry_task(store, state, policy, task_id, run_id):
    """Explicitly requeue only a retained, unpublished needs-decision trial."""
    if not state['paused'] or state.get('active'):
        raise ValueError('pause the queue and recover active work before retrying a task')
    if uuid.UUID(hex=run_id).hex != run_id:
        raise ValueError('exact trial run ID required')
    task = next((task for task in policy['tasks'] if task['id'] == task_id), None)
    entry = state['tasks'].get(task_id, {})
    if (not task or task['profiles'] or entry.get('status') != 'needs-decision'
            or any(entry.get(key) for key in ('pr', 'receipt', 'candidate_id', 'head', 'branch'))):
        raise ValueError('only an unpublished code-only needs-decision task can be retried')
    path = STATE / 'trials' / run_id / 'result.json'
    protected(path)
    trial = json.loads(path.read_text())
    if (trial.get('mode') != 'test-once' or trial.get('run_id') != run_id or trial.get('task') != task_id
            or trial.get('outcome') != 'needs-decision' or trial.get('pr') is not None
            or trial.get('status') not in {'completed', 'needs-decision', 'failed'}):
        raise ValueError('trial does not attest the exact unpublished needs-decision task')
    if any(item.get('run_id') == run_id for item in entry.get('retry_history', [])):
        raise ValueError('this trial was already retried')
    entry.setdefault('retry_history', []).append({'run_id': run_id, 'previous_status': entry['status']})
    entry['status'] = 'working'  # Keep base, checkout, edits and every old evidence file.
    store.save(state)
    print('Task requeued; evidence retained, queue still paused. Resume explicitly.')


def export_commissioning_diagnostic(run_id):
    if uuid.UUID(hex=run_id).hex != run_id:
        raise ValueError('exact commissioning ID required')
    directory = STATE / 'commissioning' / run_id
    path = directory / 'result.json'
    result = json.loads(private_diagnostic_text(path))
    if not isinstance(result, dict):
        raise ValueError('invalid commissioning phase evidence')
    phases = result.get('phases')
    allowed = {'worker-auth', 'publisher-auth', 'isolation-probe', 'worker-workspace', 'worker-tools', 'source-checks'}
    if (not isinstance(phases, dict) or not phases or type(result.get('complete')) is not bool
            or any(name not in allowed or not isinstance(status, str) or status not in {'running', 'passed', 'failed'}
                   for name, status in phases.items())):
        raise ValueError('invalid commissioning phase evidence')
    phase = next(reversed(phases))
    status = phases[phase]
    if result['complete']:
        if set(phases) != allowed or any(status != 'passed' for status in phases.values()):
            raise ValueError('inconsistent commissioning evidence')
        phase, status = 'finished', 'passed'
    code = 'none' if status == 'passed' else 'in-progress'
    evidence = ''
    if status == 'failed':
        if phase not in AUTH_PHASES:
            log = directory / (phase + ('.jsonl' if phase == 'worker-tools' else '.log'))
            logs = (log, Path(str(log) + '.stderr.log'))
            if phase == 'worker-tools':
                logs += (directory / 'worker-sandbox.log',)
            evidence = ''.join(private_diagnostic_text(item) for item in logs
                               if item.exists())
        code = diagnostic_hint(phase, evidence=evidence)
    from datetime import datetime, timezone
    observed_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    write_diagnostic('commissioning', phase, status, code, source='retained', observed_at=observed_at)
    write_operator_event(diagnostic_record('commissioning', phase, status, code,
                                         source='retained', observed_at=observed_at), evidence)
    print('Sanitized commissioning snapshot exported. Raw evidence remains private.')


def grant_log_access(username):
    """Explicit operator enrollment; never add execution accounts or alter raw logs."""
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', username):
        raise ValueError('invalid local reader account')
    user = pwd.getpwnam(username)
    if user.pw_uid < 1000 or username in ACCOUNTS.values():
        raise ValueError('log access requires a non-service, non-root local user')
    marker = STATE / 'log-access.json'
    parent = INSTALL / 'candidates' / 'operator-logs'
    protected(parent.parent)
    try:
        group = grp.getgrnam(LOG_GROUP)
    except KeyError:
        group = None
    if marker.exists() or marker.is_symlink():
        saved = json.loads(private_diagnostic_text(marker))
        if not group or saved != {'version': 1, 'group': LOG_GROUP, 'gid': group.gr_gid}:
            raise ValueError('log group differs from its retained ownership record')
    else:
        if group or parent.exists() or parent.is_symlink():
            raise ValueError('unmanaged log group/directory retained; inspect before enrollment')
        subprocess.run(['/usr/sbin/groupadd', '--system', LOG_GROUP], check=True)
        group = grp.getgrnam(LOG_GROUP)
        atomic_json(marker, {'version': 1, 'group': LOG_GROUP, 'gid': group.gr_gid})
    if group.gr_gid == 0 or any(account in group.gr_mem for account in ACCOUNTS.values()):
        raise ValueError('unsafe log reader group')
    if not parent.exists() and not parent.is_symlink():
        parent.mkdir(mode=0o700)
        os.chown(parent, 0, group.gr_gid)
        parent.chmod(0o750)
    operator_log_directory()
    if user.pw_gid != group.gr_gid and username not in group.gr_mem:
        subprocess.run(['/usr/sbin/usermod', '--append', '--groups', LOG_GROUP, username], check=True)
    print('Read-only sanitized log access granted. Start a new login session or use sg; raw evidence remains private.')


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
        ('worker-workspace', 'worker', ['python3', INSTALL / 'scripts/background-admin.py', 'probe-workspace'], 30),
        ('worker-tools', 'worker', None, 180),
        ('source-checks', 'checks', ['make', 'check'], 900),
    )
    result = {'complete': False, 'phases': {}}
    for name, role, command, seconds in phases:
        cwd = INSTALL
        if name == 'source-checks':
            cwd = INSTALL / 'candidates' / ('commission-' + evidence.name)
            result['source_tree'] = str(cwd)
        result['phases'][name] = 'running'
        atomic_json(evidence / 'result.json', result)
        print('Commissioning: ' + name, flush=True)
        emit_diagnostic('commissioning', name, 'running', 'in-progress')
        log = evidence / (name + ('.jsonl' if name == 'worker-tools' else '.log'))
        try:
            if name == 'worker-workspace':
                cwd = commissioning_workspace(evidence.name)
                result['worker_workspace'] = str(cwd)
                atomic_json(evidence / 'result.json', result)
            if name == 'source-checks':
                commissioning_source(cwd)
            if name == 'worker-tools':
                commissioning_tools(Path(result['worker_workspace']), evidence, operator['model'])
            else:
                service(role, command, cwd=cwd, seconds=seconds, output_file=log)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            if name == 'worker-tools' and not log.exists() and (evidence / 'worker-sandbox.log').exists():
                log = evidence / 'worker-sandbox.log'
            if not log.exists():
                atomic_json(log, {'error': str(error)})
            result['phases'][name] = 'failed'
            atomic_json(evidence / 'result.json', result)
            logs = (log, Path(str(log) + '.stderr.log'))
            if name == 'worker-tools':
                logs += (evidence / 'worker-sandbox.log',)
            emit_diagnostic('commissioning', name, 'failed', error=error,
                            logs=logs)
            raise ValueError(f'{name} failed; inspect private log {log}') from error
        result['phases'][name] = 'passed'
        atomic_json(evidence / 'result.json', result)
        emit_diagnostic('commissioning', name, 'passed', 'none')
    operator.update({'author_name': author_name, 'author_email': author_email, 'commissioned': True})
    atomic_json(INSTALL / 'operator.json', operator)
    result['complete'] = True
    atomic_json(evidence / 'result.json', result)
    emit_diagnostic('commissioning', 'finished', 'passed', 'none')
    print('Code-only isolation/auth checks passed. Still paused; auto-merge and live lab operations remain off.')
    print('Private commissioning evidence: ' + str(evidence))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('diagnostics', help='read sanitized last-observed diagnostics without sudo')
    logs = sub.add_parser('logs', help='read group-only sanitized operational history without sudo or rg')
    logs.add_argument('--limit', type=int, default=20)
    access = sub.add_parser('grant-log-access', help='enroll a local operator in the dedicated read-only log group')
    access.add_argument('--user', required=True)
    export = sub.add_parser('diagnostics-export', help='export one retained commissioning result without raw logs')
    export.add_argument('--commissioning', required=True)
    sub.add_parser('watchdog')
    sub.add_parser('test-once', help='launch one bounded code-only trial now; may create a PR')
    sub.add_parser('_execute-test-once', help=argparse.SUPPRESS)
    sub.add_parser('probe-isolation', help='read-only probe for the isolated checks account')
    sub.add_parser('probe-workspace', help='scratch I/O probe for the isolated worker account')
    sub.add_parser('probe-sandbox', help='fixed model-free probe for the isolated worker account')
    commission_parser = sub.add_parser('commission')
    commission_parser.add_argument('--author-name', required=True)
    commission_parser.add_argument('--author-email', required=True)
    acknowledge = sub.add_parser('acknowledge')
    acknowledge.add_argument('--run', required=True, help='exact interrupted development run ID')
    retry = sub.add_parser('retry-task', help='requeue an unpublished code-only needs-decision trial while paused')
    retry.add_argument('--task', required=True)
    retry.add_argument('--run', required=True)
    args = parser.parse_args()
    if args.action == 'logs':
        print(json.dumps(read_operator_logs(args.limit), ensure_ascii=False, indent=2))
        return
    if args.action == 'diagnostics':
        print(json.dumps(read_diagnostics(), ensure_ascii=False, indent=2))
        return
    if args.action == 'probe-isolation':
        isolation_probe()
        return
    if args.action == 'probe-workspace':
        workspace_probe()
        return
    if args.action == 'probe-sandbox':
        sandbox_probe()
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
    if args.action == 'test-once':
        launch_test_once(store, load_policy())
        return
    if args.action == '_execute-test-once':
        run(store, load_policy(), test_once=True)
        return
    if args.action == 'watchdog':
        try:
            watchdog(store)
        except ValueError as error:
            if str(error) != 'another background operation is running':
                raise
        return
    with store.locked():
        state = store.read()
        if args.action == 'grant-log-access':
            if not state.get('paused') or state.get('active'):
                raise ValueError('pause and recover active work before changing log access')
            grant_log_access(args.user)
            return
        if args.action == 'diagnostics-export':
            if state.get('active'):
                raise ValueError('cannot replace diagnostics during active development')
            export_commissioning_diagnostic(args.commissioning)
            return
        if args.action == 'retry-task':
            retry_task(store, state, load_policy(), args.task, args.run)
            return
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
