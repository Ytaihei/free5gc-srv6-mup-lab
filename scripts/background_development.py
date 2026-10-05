"""Bounded background development policy; no model output grants authority.

The source checkout is not a privileged runner. Installed runners use a
root-owned copy of this policy and a separate private state directory.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'config/background-development.yml'
SHA = re.compile(r'[0-9a-f]{40}')
TASK_ID = re.compile(r'[a-z][a-z0-9-]{0,63}')
STATES = {'queued', 'working', 'review', 'validation', 'needs-decision', 'complete'}
CHECKS = {'test', 'gitleaks', 'source-and-binaries'}
SENSITIVE_DOCS = {'AGENTS.md', 'AGENTS.ja.md', 'SECURITY.md', 'SECURITY.ja.md',
                  'LICENSE', 'LICENSE.ja.md', 'THIRD_PARTY_NOTICES.md',
                  'THIRD_PARTY_NOTICES.ja.md'}
# Only prose-oriented files; runbooks, evidence, standards and policy are manual.
AUTO_DOCS = {'README.md', 'README.ja.md', 'docs/glossary.md', 'docs/glossary.ja.md'}
PRIVATE_PARTS = {'.git', '.lab', '.ssh', '.codex', 'worktrees', 'secrets', 'artifacts'}


def load_policy(path=POLICY):
    data = yaml.safe_load(Path(path).read_text())
    fields = {'version', 'repository', 'branch', 'schedule', 'limits',
              'required_checks', 'lab_profiles', 'tasks'}
    if not isinstance(data, dict) or set(data) != fields or data['version'] != 1:
        raise ValueError('unsupported background policy')
    if data['repository'] != 'Ytaihei/free5gc-srv6-mup-lab' or data['branch'] != 'main':
        raise ValueError('unexpected publication target')
    schedule = data['schedule']
    if schedule != {'timezone': 'Asia/Tokyo', 'weekdays': [0, 2, 4], 'hour': 3,
                    'work_minutes': 40, 'recovery_minutes': 20}:
        raise ValueError('schedule changes require a reviewed runner update')
    limits = data['limits']
    maximums = {'open_prs': 3, 'changed_files': 10, 'changed_lines': 300,
                'consecutive_failures': 3}
    minimums = {'memory_available_mib': 8192, 'disk_free_gib': 30}
    if not isinstance(limits, dict) or set(limits) != set(maximums) | set(minimums):
        raise ValueError('invalid resource limits')
    for key, value in limits.items():
        if type(value) is not int or value < 1:
            raise ValueError('limits must be positive integers')
        if key in maximums and value > maximums[key] or key in minimums and value < minimums[key]:
            raise ValueError('policy cannot weaken resource/safety limits')
    if set(data['required_checks']) != CHECKS or data['lab_profiles'] != ['compact', 'reference']:
        raise ValueError('mandatory checks/profiles must be preserved')
    ids = set()
    for task in data['tasks']:
        if set(task) != {'id', 'priority', 'kind', 'depends_on', 'paths', 'acceptance', 'profiles'}:
            raise ValueError('invalid task fields')
        if not TASK_ID.fullmatch(task['id']) or task['id'] in ids:
            raise ValueError('invalid or duplicate task ID')
        ids.add(task['id'])
        if (type(task['priority']) is not int or task['kind'] not in {'maintenance', 'tests', 'feature'}
                or not isinstance(task['acceptance'], str) or not task['acceptance'].strip()
                or not isinstance(task['depends_on'], list) or not task['paths']
                or not set(task['profiles']) <= {'compact', 'reference'}):
            raise ValueError('invalid task definition')
        for name in task['paths']:
            safe_relative(name.rstrip('/'))
    graph = {task['id']: task['depends_on'] for task in data['tasks']}
    def visit(name, ancestors):
        if name not in graph or name in ancestors:
            raise ValueError('unknown or cyclic task dependency')
        for dependency in graph[name]:
            visit(dependency, ancestors | {name})
    for name in graph:
        visit(name, set())
    return data


def safe_relative(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or str(path) != name or '..' in path.parts
            or '\\' in name or any(p in PRIVATE_PARTS for p in path.parts)
            or any(ord(c) < 32 for c in name)):
        raise ValueError('unsafe candidate path')
    return path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, data):
    """Never follow a symlink or truncate an existing state file in place."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('refusing symlink state')
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.path = self.directory / 'state.json'

    def verify(self):
        for path in [self.directory, *self.directory.parents]:
            if path.is_symlink():
                raise ValueError('state directory cannot contain symlinks')
        if self.directory.exists():
            info = self.directory.stat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077):
                raise ValueError('state directory must be private and owned by the runner')

    def read(self):
        self.verify()
        if not self.path.exists():
            return {'version': 1, 'paused': True, 'failures': 0, 'tasks': {},
                    'active': None, 'last_result': None}
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
            raise ValueError('state file must be private and not a symlink')
        data = json.loads(self.path.read_text())
        if (data.get('version') != 1 or type(data.get('paused')) is not bool
                or type(data.get('failures')) is not int or data['failures'] < 0
                or not isinstance(data.get('tasks'), dict)):
            raise ValueError('invalid state')
        for key, entry in data['tasks'].items():
            if not TASK_ID.fullmatch(key) or entry.get('status') not in STATES:
                raise ValueError('invalid task state')
        return data

    @contextlib.contextmanager
    def locked(self):
        self.verify()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        with os.fdopen(os.open(self.directory / 'operation.lock', flags, 0o600), 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('another background operation is running') from error
            yield

    def save(self, data):
        self.verify()
        atomic_json(self.path, data)


def window(now=None):
    now = now or datetime.now(ZoneInfo('Asia/Tokyo'))
    if now.tzinfo is None:
        raise ValueError('clock must be timezone-aware')
    local = now.astimezone(ZoneInfo('Asia/Tokyo'))
    begin = local.replace(hour=3, minute=0, second=0, microsecond=0)
    valid_day = local.weekday() in (0, 2, 4)
    return {'work': valid_day and begin <= local < begin + timedelta(minutes=40),
            'recovery': valid_day and begin <= local < begin + timedelta(hours=1),
            'work_deadline': (begin + timedelta(minutes=40)).isoformat(),
            'recovery_deadline': (begin + timedelta(hours=1)).isoformat()}


def resources(directory, memory_text=None):
    memory = memory_text if memory_text is not None else Path('/proc/meminfo').read_text()
    entries = dict(line.split(':', 1) for line in memory.splitlines())
    parent = Path(directory)
    while not parent.exists():
        parent = parent.parent
    return {'memory_available_mib': int(entries['MemAvailable'].split()[0]) // 1024,
            'disk_free_gib': shutil.disk_usage(parent).free // (1024 ** 3)}


def select_task(policy, state):
    tasks = sorted(policy['tasks'], key=lambda item: (item['priority'], item['id']))
    for desired in ('working', 'queued'):
        for task in tasks:
            status = state['tasks'].get(task['id'], {}).get('status', 'queued')
            if status != desired:
                continue
            if all(state['tasks'].get(key, {}).get('status') == 'complete' for key in task['depends_on']):
                return task
    return None


def plan(policy, state, capacity, now=None, open_prs=0):
    reasons = []
    if state['paused']:
        reasons.append('paused')
    if state.get('active'):
        reasons.append('interrupted-run-needs-recovery')
    if not window(now)['work']:
        reasons.append('outside-work-window')
    if state['failures'] >= policy['limits']['consecutive_failures']:
        reasons.append('failure-limit')
    for key in ('memory_available_mib', 'disk_free_gib'):
        if capacity[key] < policy['limits'][key]:
            reasons.append('insufficient-' + key.replace('_', '-'))
    task = select_task(policy, state)
    if open_prs >= policy['limits']['open_prs'] and (not task or
            state['tasks'].get(task['id'], {}).get('status') != 'working'):
        reasons.append('open-pr-limit')
    if task is None:
        reasons.append('no-ready-task')
    return {'eligible': not reasons, 'reasons': reasons,
            'task': task['id'] if task else None, 'window': window(now),
            'capacity': capacity, 'publication_enabled': False, 'lab_enabled': False}


def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c',
                                    'core.fsmonitor=false', '-C', str(repo), *args], text=True)


def changes(repo, base, head):
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError('immutable full commit IDs are required')
    if git(repo, 'merge-base', base, head).strip() != base:
        raise ValueError('candidate must descend from its recorded base')
    names = git(repo, 'diff', '--no-ext-diff', '--no-renames', '--name-only', '-z', base, head).split('\0')
    result = []
    for name in filter(None, names):
        safe_relative(name)
        entries = git(repo, 'ls-tree', head, '--', name).strip()
        if not entries or entries.split()[0] != '100644':
            raise ValueError('deleted, executable, symlink and submodule changes require human review')
        num = git(repo, 'diff', '--no-ext-diff', '--no-renames', '--numstat', base, head, '--', name).split('\t')
        if len(num) < 3 or not num[0].isdigit() or not num[1].isdigit():
            raise ValueError('binary changes require human review')
        result.append({'path': name, 'added': int(num[0]), 'deleted': int(num[1])})
    return result


def classify(entries, policy):
    """Conservative eligibility, NOT a substitute for immutable semantic review."""
    reasons = []
    if not entries:
        reasons.append('empty-change')
    if len(entries) > policy['limits']['changed_files']:
        reasons.append('too-many-files')
    if sum(entry['added'] + entry['deleted'] for entry in entries) > policy['limits']['changed_lines']:
        reasons.append('too-many-lines')
    for entry in entries:
        name = entry['path']
        safe_relative(name)
        if name in AUTO_DOCS:
            continue
        if name in {'config/documentation.json', 'config/public-source.json'}:
            continue  # Exact mechanical regeneration checked separately.
        if ((name.startswith('internal/') and name.endswith('_test.go'))
                or re.fullmatch(r'tests/test_[a-z0-9_]+\.py', name)) and entry['deleted'] == 0:
            continue
        reasons.append('manual-path-or-test-edit')
    return {'eligible': not reasons, 'reasons': sorted(set(reasons))}


def merge_gate(policy, receipt, pr, check_runs, now):
    """Receipt is written by the trusted coordinator, never the coding worker."""
    if (not receipt.get('eligible') or not receipt.get('local_checks_passed')
            or not receipt.get('review_passed') or not receipt.get('translation_checked')
            or receipt.get('requires_live_validation') or receipt.get('decision_required')):
        return False
    head = receipt.get('head', '')
    if not SHA.fullmatch(head) or pr.get('head', {}).get('sha') != head:
        return False
    if (pr.get('state') != 'open' or pr.get('draft') or pr.get('mergeable') is not True
            or pr.get('mergeable_state') != 'clean' or pr.get('base', {}).get('ref') != 'main'
            or pr.get('base', {}).get('sha') != receipt.get('base')
            or pr.get('head', {}).get('repo', {}).get('full_name') != policy['repository']
            or pr.get('head', {}).get('ref') != receipt.get('branch')):
        return False
    try:
        created = datetime.fromisoformat(receipt['reviewed_at'])
        age = now - created
    except (ValueError, KeyError, TypeError):
        return False
    if not timedelta(0) <= age <= timedelta(days=7):
        return False
    for name in policy['required_checks']:
        matches = [run for run in check_runs if run.get('name') == name and run.get('head_sha') == head
                   and run.get('app', {}).get('id') == 15368]
        if not matches:
            return False
        latest = max(matches, key=lambda run: run['id'])
        if latest.get('status') != 'completed' or latest.get('conclusion') != 'success':
            return False
    return True


def public_summary(task_id, result, pr_number=None):
    """A fixed template: never publish arbitrary agent output or raw exceptions."""
    if not TASK_ID.fullmatch(task_id):
        raise ValueError('invalid public task ID')
    if result not in {'review', 'merged', 'blocked', 'failed', 'recovered'}:
        raise ValueError('invalid public result')
    if pr_number is not None and (type(pr_number) is not int or pr_number < 1):
        raise ValueError('invalid PR number')
    return f'Background development: `{task_id}` — {result}.' + (f' PR #{pr_number}.' if pr_number else '')
