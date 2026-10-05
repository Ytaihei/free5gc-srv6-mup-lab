"""Offline contract tests. These do not attest privileged installation or lab recovery."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import background_development as bg
import background_executor as executor

INSTALLER_SPEC = importlib.util.spec_from_file_location('background_installer', ROOT / 'scripts/install-background-development.py')
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
INSTALLER_SPEC.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.unit_directory = self.directory / 'units'
        self.unit_directory.mkdir()
        unit_patch = patch.object(installer, 'UNIT_DIRECTORY', self.unit_directory)
        unit_patch.start()
        self.addCleanup(unit_patch.stop)
        systemctl_patch = patch.object(installer.subprocess, 'check_output', side_effect=self.show)
        self.systemctl = systemctl_patch.start()
        self.addCleanup(systemctl_patch.stop)

    def show(self, argv, **kwargs):
        if '--property=ActiveState' in argv:
            return 'inactive\n'
        if '--property=FragmentPath' in argv:
            path = self.unit_directory / argv[2]
            return str(path) + '\n' if path.exists() else ''
        return ''

    def copy_unit(self):
        path = self.unit_directory / installer.UNITS[0]
        path.write_bytes((ROOT / 'configs/systemd' / path.name).read_bytes())
        path.chmod(0o644)
        return path

    @staticmethod
    def protected_stat(path, *args, **kwargs):
        info = os.lstat(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink)

    def test_fresh_install_has_no_units_to_adopt(self):
        self.assertEqual(installer.check_units(), [])

    def test_existing_unit_refuses_before_any_install_mutation(self):
        self.copy_unit()
        with patch.object(installer, 'INSTALL', self.directory / 'install'), \
                patch.object(installer, 'STATE', self.directory / 'state'), \
                patch.object(installer.os, 'geteuid', return_value=0), \
                patch.object(sys, 'argv', ['installer', '--apply']), \
                patch.object(installer.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'existing systemd unit'):
                installer.main()
            run.assert_not_called()
            self.assertFalse((self.directory / 'install').exists())
            self.assertFalse((self.directory / 'state').exists())

    def test_exact_protected_template_can_be_adopted(self):
        path = self.copy_unit()
        with patch.object(Path, 'lstat', self.protected_stat):
            self.assertEqual(installer.check_units(adopt=True), [path.name])

    def test_custom_unit_is_preserved(self):
        path = self.copy_unit()
        path.write_text('[Service]\nExecStart=/custom/program\n')
        with patch.object(Path, 'lstat', self.protected_stat):
            with self.assertRaisesRegex(ValueError, 'exact protected template'):
                installer.check_units(adopt=True)
        self.assertIn('/custom/program', path.read_text())

    def test_symlink_unit_refused(self):
        path = self.unit_directory / installer.UNITS[0]
        path.symlink_to(ROOT / 'configs/systemd' / path.name)
        with patch.object(Path, 'lstat', self.protected_stat):
            with self.assertRaisesRegex(ValueError, 'exact protected template'):
                installer.check_units(adopt=True)

    def test_unprotected_template_refused(self):
        path = self.copy_unit()
        path.chmod(0o666)
        with patch.object(Path, 'lstat', self.protected_stat):
            with self.assertRaisesRegex(ValueError, 'exact protected template'):
                installer.check_units(adopt=True)

    def test_drop_in_refused(self):
        self.systemctl.side_effect = lambda argv, **kwargs: '/override.conf\n' if '--property=DropInPaths' in argv else self.show(argv)
        with self.assertRaisesRegex(ValueError, 'drop-ins'):
            installer.check_units(adopt=True)

    def test_vendor_unit_refused(self):
        self.systemctl.side_effect = lambda argv, **kwargs: '/usr/lib/systemd/system/custom.service\n'
        with self.assertRaisesRegex(ValueError, 'outside the installation'):
            installer.check_units(adopt=True)

    def test_running_service_refused(self):
        self.systemctl.side_effect = lambda argv, **kwargs: 'active\n' if '--property=ActiveState' in argv else self.show(argv)
        with self.assertRaisesRegex(ValueError, 'must be inactive'):
            installer.check_units(adopt=True)


class BackgroundPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = bg.load_policy()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private'
        self.store = bg.Store(self.directory)
        self.state = self.store.read()
        self.now = datetime(2026, 10, 4, 18, 10, tzinfo=timezone.utc)  # Monday 03:10 JST
        self.capacity = {'memory_available_mib': 10000, 'disk_free_gib': 40}

    def test_default_is_paused_and_read_only(self):
        self.assertTrue(self.state['paused'])
        self.assertFalse(self.directory.exists())
        result = bg.plan(self.policy, self.state, self.capacity, self.now)
        self.assertIn('paused', result['reasons'])
        self.assertFalse(self.directory.exists())

    def test_window_boundaries_and_timezone(self):
        self.assertTrue(bg.window(self.now)['work'])
        self.assertFalse(bg.window(self.now.replace(minute=40))['work'])
        self.assertTrue(bg.window(self.now.replace(minute=40))['recovery'])
        self.assertFalse(bg.window(self.now.replace(hour=19, minute=0))['recovery'])
        self.assertFalse(bg.window(self.now + timedelta(days=1))['work'])
        with self.assertRaises(ValueError):
            bg.window(datetime(2026, 10, 5, 3))

    def test_resource_gates(self):
        self.state['paused'] = False
        for field, value in [('memory_available_mib', 8191), ('disk_free_gib', 29)]:
            capacity = dict(self.capacity, **{field: value})
            self.assertFalse(bg.plan(self.policy, self.state, capacity, self.now)['eligible'])

    def test_stops_for_unacknowledged_interruption(self):
        self.state.update({'paused': False, 'active': {'id': 'unacknowledged'}})
        self.assertIn('interrupted-run-needs-recovery', bg.plan(
            self.policy, self.state, self.capacity, self.now)['reasons'])

    def test_failure_limit(self):
        self.state.update({'paused': False, 'failures': 3})
        self.assertIn('failure-limit', bg.plan(self.policy, self.state, self.capacity, self.now)['reasons'])

    def test_open_pr_limit_and_continuation(self):
        self.state['paused'] = False
        result = bg.plan(self.policy, self.state, self.capacity, self.now, open_prs=3)
        self.assertIn('open-pr-limit', result['reasons'])
        self.state['tasks']['documentation-maintenance'] = {'status': 'working'}
        self.assertTrue(bg.plan(self.policy, self.state, self.capacity, self.now, open_prs=3)['eligible'])

    def test_working_before_new_task(self):
        self.state['tasks']['pfcp-unit-coverage'] = {'status': 'working'}
        self.assertEqual(bg.select_task(self.policy, self.state)['id'], 'pfcp-unit-coverage')

    def test_dependency_requires_merged_completion(self):
        for task in self.policy['tasks']:
            self.state['tasks'][task['id']] = {'status': 'needs-decision'}
        self.state['tasks']['convergence-measurement'] = {'status': 'queued'}
        self.state['tasks']['convergence-measurement-design'] = {'status': 'review'}
        self.assertIsNone(bg.select_task(self.policy, self.state))
        self.state['tasks']['convergence-measurement-design']['status'] = 'complete'
        self.assertEqual(bg.select_task(self.policy, self.state)['id'], 'convergence-measurement')

    def test_policy_rejects_weaker_limits(self):
        import yaml
        policy = copy.deepcopy(self.policy)
        policy['limits']['open_prs'] = 100
        path = Path(self.temp.name) / 'policy.yml'
        path.write_text(yaml.safe_dump(policy))
        with self.assertRaises(ValueError):
            bg.load_policy(path)

    def test_policy_rejects_dependency_cycle(self):
        import yaml
        policy = copy.deepcopy(self.policy)
        policy['tasks'][0]['depends_on'] = [policy['tasks'][0]['id']]
        path = Path(self.temp.name) / 'policy.yml'
        path.write_text(yaml.safe_dump(policy))
        with self.assertRaises(ValueError):
            bg.load_policy(path)

    def test_state_atomic_and_private(self):
        with self.store.locked():
            self.store.save(self.state)
        self.assertEqual(self.store.read(), self.state)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)

    def test_state_symlink_rejected(self):
        self.directory.symlink_to(self.temp.name)
        with self.assertRaises(ValueError):
            self.store.read()

    def test_state_permissions_rejected(self):
        self.directory.mkdir(mode=0o755)
        self.directory.chmod(0o755)
        with self.assertRaises(ValueError):
            self.store.read()

    def test_state_file_symlink_rejected(self):
        self.directory.mkdir(mode=0o700)
        other = self.directory / 'other'
        other.write_text(json.dumps(self.state))
        self.store.path.symlink_to(other)
        with self.assertRaises(ValueError):
            self.store.read()

    def test_nonblocking_lock(self):
        with self.store.locked():
            with self.assertRaisesRegex(ValueError, 'another background'):
                with self.store.locked():
                    pass

    def test_cli_preview_does_not_create_state(self):
        for action in (['plan'], ['status'], ['run', '--dry-run']):
            result = subprocess.run([sys.executable, ROOT / 'scripts/background-development.py',
                                     '--state', self.directory, *action], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['eligible'])
        self.assertFalse(self.directory.exists())

    def test_checkout_cannot_run_or_resume(self):
        for action in ('run', 'resume'):
            result = subprocess.run([sys.executable, ROOT / 'scripts/background-development.py',
                                     '--state', self.directory, action], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('installed coordinator', result.stderr)


class MergeGateTests(unittest.TestCase):
    def setUp(self):
        self.policy = bg.load_policy()
        self.now = datetime.now(timezone.utc)
        self.receipt = {'eligible': True, 'local_checks_passed': True, 'review_passed': True,
                        'translation_checked': True, 'requires_live_validation': False,
                        'decision_required': False, 'head': 'a' * 40, 'base': 'b' * 40,
                        'branch': 'automation/test-12345678', 'reviewed_at': self.now.isoformat()}
        self.pr = {'head': {'sha': 'a' * 40, 'ref': self.receipt['branch'],
                            'repo': {'full_name': self.policy['repository']}},
                   'base': {'ref': 'main', 'sha': 'b' * 40}, 'state': 'open',
                   'draft': False, 'mergeable': True, 'mergeable_state': 'clean'}
        self.checks = [{'id': index, 'name': name, 'head_sha': 'a' * 40, 'app': {'id': 15368},
                        'status': 'completed', 'conclusion': 'success'}
                       for index, name in enumerate(self.policy['required_checks'])]

    def gate(self):
        return bg.merge_gate(self.policy, self.receipt, self.pr, self.checks, self.now)

    def test_all_evidence_required(self):
        self.assertTrue(self.gate())
        for field in ('eligible', 'local_checks_passed', 'review_passed', 'translation_checked'):
            self.receipt[field] = False
            self.assertFalse(self.gate())
            self.receipt[field] = True

    def test_requires_human_for_runtime(self):
        self.receipt['requires_live_validation'] = True
        self.assertFalse(self.gate())

    def test_requires_human_for_decision(self):
        self.receipt['decision_required'] = True
        self.assertFalse(self.gate())

    def test_head_and_base_are_immutable(self):
        self.pr['head']['sha'] = 'c' * 40
        self.assertFalse(self.gate())
        self.pr['head']['sha'] = 'a' * 40
        self.pr['base']['sha'] = 'c' * 40
        self.assertFalse(self.gate())

    def test_other_repository_branch_or_draft_rejected(self):
        for field, value in [('draft', True), ('state', 'closed'), ('mergeable_state', 'blocked')]:
            previous = self.pr[field]
            self.pr[field] = value
            self.assertFalse(self.gate())
            self.pr[field] = previous
        self.pr['head']['repo']['full_name'] = 'someone/fork'
        self.assertFalse(self.gate())

    def test_missing_pending_failed_or_fake_check_rejected(self):
        original = copy.deepcopy(self.checks)
        for field, value in [('status', 'in_progress'), ('conclusion', 'failure'),
                             ('app', {'id': 1}), ('head_sha', 'c' * 40)]:
            self.checks = copy.deepcopy(original)
            self.checks[0][field] = value
            self.assertFalse(self.gate())
        self.checks = original[1:]
        self.assertFalse(self.gate())

    def test_latest_rerun_wins(self):
        latest = dict(self.checks[0], id=100, conclusion='failure')
        self.checks.append(latest)
        self.assertFalse(self.gate())

    def test_future_and_old_review_rejected(self):
        for delta in (timedelta(days=8), -timedelta(seconds=1)):
            self.receipt['reviewed_at'] = (self.now - delta).isoformat()
            self.assertFalse(self.gate())

    def test_policy_path_and_diff_boundaries(self):
        for name in ('go.mod', 'scripts/compact_runtime.py', 'SECURITY.md', 'AGENTS.md',
                     '.github/workflows/ci.yml', 'docs/validation-summary.md'):
            self.assertFalse(bg.classify([{'path': name, 'added': 1, 'deleted': 0}], self.policy)['eligible'])
        self.assertTrue(bg.classify([{'path': 'internal/pfcpstate/state_test.go', 'added': 10,
                                     'deleted': 0}], self.policy)['eligible'])
        self.assertFalse(bg.classify([{'path': 'internal/pfcpstate/state_test.go', 'added': 10,
                                      'deleted': 1}], self.policy)['eligible'])
        self.assertFalse(bg.classify([{'path': 'README.md', 'added': 301, 'deleted': 0}], self.policy)['eligible'])
        self.assertFalse(bg.classify([], self.policy)['eligible'])

    def test_public_summary_does_not_accept_arbitrary_output(self):
        self.assertEqual(bg.public_summary('task', 'review', 5), 'Background development: `task` — review. PR #5.')
        for identifier in ('../../secret', 'host@private', 'task\nsecret'):
            with self.assertRaises(ValueError):
                bg.public_summary(identifier, 'review')


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.source.mkdir()
        self.target.mkdir()

    def test_plain_snapshot_ignores_git(self):
        (self.source / '.git').mkdir()
        (self.source / '.git/config').write_text('untrusted metadata')
        (self.source / 'example.py').write_text('print(1)')
        executor.snapshot(self.source, self.target)
        self.assertFalse((self.target / '.git').exists())
        self.assertEqual((self.target / 'example.py').read_text(), 'print(1)')

    def test_snapshot_and_git_readable_under_private_service_umask(self):
        (self.source / 'nested').mkdir()
        (self.source / 'nested/example.py').write_text('print(1)')
        previous = os.umask(0o077)
        try:
            executor.snapshot(self.source, self.target)
            executor.command(['git', 'init', '-q', self.target])
            executor.command(['git', '-C', self.target, 'add', '--all'])
            executor.command(['git', '-C', self.target, '-c', 'user.name=Test',
                              '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'candidate'])
        finally:
            os.umask(previous)
        # Roles with different UIDs must traverse/read but never modify it.
        for path in [self.target, *self.target.rglob('*')]:
            mode = path.stat().st_mode
            self.assertEqual(mode & 0o022, 0, str(path))
            self.assertEqual(mode & 0o044, 0o044, str(path))
            if path.is_dir():
                self.assertEqual(mode & 0o011, 0o011, str(path))

    def test_symlink_file_rejected(self):
        (self.source / 'link').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            executor.snapshot(self.source, self.target)

    def test_symlink_directory_rejected(self):
        (self.source / 'link').symlink_to('/etc', target_is_directory=True)
        with self.assertRaises(ValueError):
            executor.snapshot(self.source, self.target)

    def test_private_state_rejected(self):
        (self.source / '.lab').mkdir()
        with self.assertRaises(ValueError):
            executor.snapshot(self.source, self.target)

    def test_hardlink_rejected(self):
        (self.root / 'other').write_text('retained')
        os.link(self.root / 'other', self.source / 'link')
        with self.assertRaises(ValueError):
            executor.snapshot(self.source, self.target)

    def test_bytes_limit(self):
        with (self.source / 'large').open('wb') as stream:
            stream.truncate(65 * 1024 * 1024)
        with self.assertRaises(ValueError):
            executor.snapshot(self.source, self.target)


class ServiceBoundaryTests(unittest.TestCase):
    def test_checker_has_no_credentials_or_host_sockets(self):
        with patch.object(executor.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{}'
            executor.service('checks', ['make', 'check'], cwd=Path('/opt/candidate'), seconds=50)
            args = run.call_args_list[0].args[0]
            joined = ' '.join(args)
            self.assertIn('User=mup-bg-checks', joined)
            self.assertIn('NoNewPrivileges=yes', joined)
            self.assertIn('PrivateDevices=yes', joined)
            self.assertIn('MemorySwapMax=0', joined)
            self.assertIn('-/var/lib/mup-bg-worker', joined)
            self.assertIn('-/var/lib/mup-bg-publisher', joined)
            self.assertIn('-/run/docker.sock', joined)
            self.assertIn('100.64.0.0/10', joined)
            self.assertNotIn('OPENAI_API_KEY', joined)
            self.assertNotIn('GH_TOKEN', joined)

    def test_unapproved_github_target_fails_before_execution(self):
        with patch.object(executor, 'service') as service:
            with self.assertRaises(ValueError):
                executor.github(bg.load_policy(), 'repos/someone/other/pulls')
            service.assert_not_called()

    def test_systemd_schedule_has_no_catchup(self):
        text = (ROOT / 'configs/systemd/srv6-mup-background.timer').read_text()
        self.assertIn('Mon,Wed,Fri *-*-* 03:00:00 Asia/Tokyo', text)
        self.assertIn('Persistent=false', text)

    def test_no_live_permission_in_initial_operator(self):
        text = (ROOT / 'scripts/install-background-development.py').read_text()
        self.assertIn("'commissioned': False", text)
        self.assertIn("'automatic_merge': False", text)
        self.assertIn("'lab_rehearsals': {'compact': False, 'reference': False}", text)
        self.assertNotIn("'enable', '--now'", text)


class CoordinatorIntegrationTests(unittest.TestCase):
    """Real local Git/snapshots with fake model, systemd and GitHub transports."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        self.policy = bg.load_policy()
        self.state_dir = self.root / 'state'
        self.state_dir.mkdir(mode=0o700)
        self.store = bg.Store(self.state_dir)
        self.state = self.store.read()
        self.install = self.root / 'install'
        self.install.mkdir()
        self.worker = self.root / 'worker'
        self.worker.mkdir()
        for name in ('README.md', 'README.ja.md'):
            (self.origin / name).write_text('old prose\n')
        (self.origin / 'config').mkdir()
        public = {'version': 1, 'files': ['README.md', 'README.ja.md', 'config/public-source.json',
                                        'config/documentation.json'], 'private_only': []}
        (self.origin / 'config/public-source.json').write_text(json.dumps(public, indent=2) + '\n')
        docs = {'version': 1, 'translations': [{'source': 'README.md', 'translation': 'README.ja.md',
                  'kind': 'markdown', 'source_sha256': bg.digest(self.origin / 'README.md'),
                  'translation_sha256': bg.digest(self.origin / 'README.ja.md')}]}
        (self.origin / 'config/documentation.json').write_text(json.dumps(docs, indent=2) + '\n')
        self.git('init', '-q')
        self.git('add', '--all')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        self.operator = {'model': 'test-model', 'author_name': 'Test', 'author_email': 'test@example.invalid'}
        self.events = []
        self.patches = [patch.object(executor, 'INSTALL', self.install),
                        patch.object(executor, 'STATE', self.state_dir),
                        patch.object(executor, 'HOMES', {'worker': self.worker}),
                        patch.object(executor.pwd, 'getpwnam', return_value=executor.pwd.getpwuid(os.getuid())),
                        patch.object(executor, 'service', side_effect=self.service),
                        patch.object(executor, 'github', side_effect=self.github),
                        patch.object(executor, 'work_budget', return_value=300)]
        real_command = executor.command
        def command(argv, **kwargs):
            argv = list(map(str, argv))
            if 'clone' in argv:
                argv[-2] = str(self.origin)
            if 'fetch' in argv:
                argv[argv.index('fetch') + 2] = str(self.origin) if '--no-tags' in argv else argv[argv.index('fetch') + 2]
            return real_command(argv, **kwargs)
        self.patches.append(patch.object(executor, 'command', side_effect=command))
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.origin), *args], text=True).strip()

    def service(self, role, argv, **kwargs):
        self.events.append((role, list(map(str, argv))))
        if role == 'worker':
            if '--output-schema' in argv:
                path = Path(argv[argv.index('-o') + 1])
                path.write_text(json.dumps({'review_passed': True, 'translation_checked': True,
                                           'decision_required': False, 'requires_live_validation': False}))
            else:
                for name in ('README.md', 'README.ja.md'):
                    (kwargs['cwd'] / name).write_text('corrected prose\n')
        return ''

    def github(self, policy, endpoint, method='GET', fields=None):
        self.events.append(('github', method, endpoint))
        if method == 'GET':
            return []
        return {'number': 12}

    def test_development_freezes_checks_reviews_and_creates_one_pr(self):
        executor.develop(self.store, self.state, self.policy, self.operator,
                         self.policy['tasks'][0], 'a' * 32, 'unused')
        entry = self.state['tasks']['documentation-maintenance']
        self.assertEqual(entry['pr'], 12)
        self.assertEqual(entry['status'], 'review')
        self.assertTrue(entry['receipt']['eligible'])
        self.assertTrue(entry['receipt']['review_passed'])
        self.assertEqual(len([event for event in self.events if event[0] == 'checks']), 5)
        self.assertEqual(len([event for event in self.events if event[0] == 'worker']), 2)
        persisted = self.store.read()['tasks']['documentation-maintenance']
        self.assertEqual(persisted['head'], entry['head'])

    def test_retry_publication_reuses_frozen_candidate(self):
        executor.develop(self.store, self.state, self.policy, self.operator,
                         self.policy['tasks'][0], 'b' * 32, 'unused')
        entry = self.state['tasks']['documentation-maintenance']
        first_head, first_branch = entry['head'], entry['branch']
        del entry['pr']
        entry['status'] = 'working'
        self.events.clear()
        executor.develop(self.store, self.state, self.policy, self.operator,
                         self.policy['tasks'][0], 'c' * 32, 'unused')
        self.assertEqual(entry['head'], first_head)
        self.assertEqual(entry['branch'], first_branch)
        self.assertFalse(any(event[0] == 'worker' for event in self.events))


if __name__ == '__main__':
    unittest.main()
