"""Offline contract tests. These do not attest privileged installation or lab recovery."""
import copy
import contextlib
import errno
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import io
import os
import re
from pathlib import Path
from types import SimpleNamespace
import subprocess
import socket
import sys
import tarfile
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
ADMIN_SPEC = importlib.util.spec_from_file_location('background_admin', ROOT / 'scripts/background-admin.py')
admin = importlib.util.module_from_spec(ADMIN_SPEC)
ADMIN_SPEC.loader.exec_module(admin)


class SafeDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / 'install'
        (self.install / 'candidates').mkdir(parents=True)
        self.state = self.root / 'private'
        self.state.mkdir(mode=0o700)
        for mocked in (patch.object(executor, 'INSTALL', self.install),
                       patch.object(executor, 'ROOT', self.install),
                       patch.object(executor.os, 'geteuid', return_value=0),
                       patch.object(executor, 'protected'), patch.object(admin, 'protected'),
                       patch.object(admin, 'STATE', self.state)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def record_path(self, scope='commissioning'):
        return self.install / 'candidates/diagnostics' / (scope + '.json')

    def test_whitelist_projection_never_exports_private_text(self):
        secret = 'PRIVATE_SENTINEL /home/private-host api-key-placeholder person@example.invalid 192.0.2.12'
        log = self.state / 'worker.jsonl'
        bg.atomic_json(log, {'type': 'error', 'message': secret + ' bwrap: operation not permitted'})
        original = log.read_bytes()
        executor.emit_diagnostic('commissioning', 'worker-tools', 'failed',
                                 error=ValueError(secret), logs=(log,))
        result = executor.read_diagnostics()['commissioning']
        self.assertEqual(result['classification_hint'], 'sandbox-denied')
        self.assertEqual(result['next_action'], 'operator-inspect-isolation-do-not-disable')
        public = self.record_path().read_text()
        for value in secret.split():
            self.assertNotIn(value, public)
        self.assertNotIn(str(log), public)
        self.assertEqual(log.read_bytes(), original)
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_public_permissions_under_private_umask_and_atomic_replacement(self):
        previous = os.umask(0o077)
        try:
            executor.write_diagnostic('commissioning', 'worker-tools', 'running', 'in-progress')
            executor.write_diagnostic('commissioning', 'worker-tools', 'failed', 'tool-execution-error')
        finally:
            os.umask(previous)
        path = self.record_path()
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(json.loads(path.read_text())['status'], 'failed')
        self.assertEqual(list(path.parent.glob('.diagnostic-*')), [])

    def test_invalid_enum_and_uninstalled_writer_are_refused(self):
        for args in [('private', 'preflight', 'failed', 'none'),
                     ('commissioning', '/private/path', 'failed', 'none'),
                     ('commissioning', 'preflight', 'secret-text', 'none'),
                     ('commissioning', 'preflight', 'failed', 'private-message')]:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'fixed enums'):
                executor.write_diagnostic(*args)
        with patch.object(executor.os, 'geteuid', return_value=1000):
            with self.assertRaisesRegex(ValueError, 'installed coordinator'):
                executor.write_diagnostic('commissioning', 'preflight', 'failed', 'unknown-failure')
        self.assertFalse(self.record_path().exists())

    def test_classifier_returns_only_known_hints(self):
        cases = [('codex-code-mode-host: No such file', None, 'tool-host-missing'),
                 ('bwrap: loopback: Failed to create NETLINK_ROUTE socket: Address family not supported by protocol',
                  ValueError('failed'), 'sandbox-address-family-denied'),
                 ('sandbox: Permission denied', None, 'sandbox-denied'),
                 ('{"type": "error", "message": "private"}', None, 'tool-execution-error'),
                 ('', executor.WorkerExecutionError('incomplete event private'), 'invalid-worker-events'),
                 ('', FileNotFoundError('private'), 'artifact-unverified'),
                 ('', subprocess.TimeoutExpired('private command', 3), 'timeout'),
                 ('unrecognized secret error', None, 'unknown-failure')]
        for evidence, error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(executor.diagnostic_hint('worker-tools', error, evidence), expected)

    def test_reader_requires_no_private_state_or_subprocess_and_marks_old_snapshot(self):
        executor.write_diagnostic('commissioning', 'worker-tools', 'failed', 'unknown-failure',
                                  source='retained', observed_at='2020-01-01T00:00:00+00:00')
        with patch.object(bg.Store, 'read', side_effect=AssertionError('private state read')), \
                patch.object(executor.subprocess, 'run') as command:
            result = executor.read_diagnostics()
            command.assert_not_called()
        self.assertTrue(result['commissioning']['stale_or_clock_skew'])
        self.assertEqual(result['development']['status'], 'unavailable')
        self.assertIn('not-live-state', result['notice'])

    def test_reader_rejects_extra_fields_invalid_timestamp_and_large_file(self):
        executor.write_diagnostic('commissioning', 'worker-tools', 'failed', 'unknown-failure')
        path = self.record_path()
        original = json.loads(path.read_text())
        for invalid in [dict(original, secret='PRIVATE_SENTINEL'),
                        dict(original, observed_at='PRIVATE_SENTINEL'),
                        dict(original, next_action='PRIVATE_SENTINEL'), ['PRIVATE_SENTINEL']]:
            path.write_text(json.dumps(invalid))
            result = executor.read_diagnostics()
            self.assertEqual(result['commissioning']['status'], 'unavailable')
            self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))
        path.write_text('X' * 8193)
        self.assertEqual(executor.read_diagnostics()['commissioning']['status'], 'unavailable')

    def test_link_destinations_and_private_evidence_links_are_refused(self):
        executor.write_diagnostic('commissioning', 'preflight', 'failed', 'unknown-failure')
        path = self.record_path()
        path.unlink()
        target = self.state / 'secret'
        target.write_text('PRIVATE_SENTINEL')
        target.chmod(0o600)
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            executor.write_diagnostic('commissioning', 'preflight', 'failed', 'unknown-failure')
        self.assertEqual(executor.read_diagnostics()['commissioning']['status'], 'unavailable')
        with self.assertRaises(OSError):
            executor.private_diagnostic_text(path)
        self.assertEqual(target.read_text(), 'PRIVATE_SENTINEL')

    def test_export_failure_is_generic_and_does_not_mask_original_operation(self):
        with patch.object(executor, 'write_diagnostic', side_effect=OSError('PRIVATE_SENTINEL')), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            executor.emit_diagnostic('commissioning', 'worker-tools', 'failed', 'unknown-failure')
        self.assertNotIn('PRIVATE_SENTINEL', output.getvalue())
        self.assertIn('unavailable', output.getvalue())

    def test_retained_export_does_not_execute_or_modify_private_evidence(self):
        run_id = 'a' * 32
        directory = self.state / 'commissioning' / run_id
        directory.mkdir(parents=True)
        result = directory / 'result.json'
        bg.atomic_json(result, {'complete': False, 'phases': {'worker-auth': 'passed', 'worker-tools': 'failed'},
                                'worker_workspace': '/private/host/path'})
        log = directory / 'worker-tools.jsonl'
        bg.atomic_json(log, {'type': 'error', 'message': 'codex-code-mode-host not found PRIVATE_SENTINEL'})
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        with patch.object(admin, 'service') as service:
            admin.export_commissioning_diagnostic(run_id)
            service.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})
        diagnostic = executor.read_diagnostics()['commissioning']
        self.assertEqual(diagnostic['classification_hint'], 'tool-host-missing')
        self.assertEqual(diagnostic['source'], 'retained')
        self.assertNotIn('/private', json.dumps(diagnostic))
        for invalid in ('../../private', 'A' * 32):
            with self.assertRaises(ValueError):
                admin.export_commissioning_diagnostic(invalid)
        for invalid in ([], {}, {'complete': False, 'phases': {'worker-tools': []}}):
            bg.atomic_json(result, invalid)
            with self.assertRaisesRegex(ValueError, 'invalid commissioning phase evidence'):
                admin.export_commissioning_diagnostic(run_id)

    def test_diagnostics_cli_is_read_only_and_export_requires_installed_root(self):
        with patch.object(sys, 'argv', ['background-admin.py', 'diagnostics']), \
                patch.object(admin, 'read_diagnostics', return_value={'status': 'unavailable'}), \
                patch.object(bg.Store, 'read', side_effect=AssertionError('private read')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            admin.main()
        self.assertEqual(json.loads(output.getvalue()), {'status': 'unavailable'})
        with patch.object(sys, 'argv', ['background-admin.py', 'diagnostics-export', '--commissioning', 'a' * 32]):
            with self.assertRaisesRegex(ValueError, 'root-owned installed'):
                admin.main()


class OperatorLogTests(unittest.TestCase):
    def setUp(self):
        SafeDiagnosticTests.setUp(self)
        self.gid = os.getgid()
        self.group = SimpleNamespace(gr_gid=self.gid, gr_mem=[])
        self.parent = self.install / 'candidates/operator-logs'
        self.parent.mkdir(mode=0o750)
        self.parent.chmod(0o750)
        for mocked in (patch.object(executor.grp, 'getgrnam', return_value=self.group),
                       patch.object(executor.os, 'fchown'),
                       patch.object(admin, 'INSTALL', self.install)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def record(self, phase='worker-tools'):
        return executor.diagnostic_record('commissioning', phase, 'failed', 'sandbox-denied')

    def test_detailed_signals_and_no_raw_text_or_model_response(self):
        with self.assertRaises(ValueError):
            executor.write_operator_event(dict(self.record(), secret='PRIVATE_SECRET'))
        self.assertEqual(list(self.parent.iterdir()), [])
        text = 'bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\nPRIVATE_SECRET /home/person 192.0.2.1'
        executor.write_operator_event(self.record(), text)
        result = executor.read_operator_logs()
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['events'][0]['signals'],
                         ['bwrap', 'loopback', 'netlink-address', 'operation-not-permitted'])
        self.assertNotIn('PRIVATE_SECRET', json.dumps(result))
        self.assertNotIn('/home/person', json.dumps(result))
        self.assertNotIn('192.0.2.1', json.dumps(result))

    def test_authentication_evidence_is_never_read_or_projected(self):
        bg.atomic_json(self.state / 'auth.log', {'message': 'PRIVATE_SECRET bwrap permission denied'})
        for phase in executor.AUTH_PHASES:
            with patch.object(executor, 'private_diagnostic_text', side_effect=AssertionError('auth read')):
                executor.emit_diagnostic('commissioning', phase, 'failed', error=ValueError('private'),
                                         logs=(self.state / 'auth.log',))
            self.assertEqual(executor.operator_event(self.record(phase), 'bwrap permission denied')['signals'], [])
        self.assertTrue(all(e['evidence'] == 'excluded-auth' for e in executor.read_operator_logs()['events']))

    def test_atomic_group_only_permissions_under_private_umask(self):
        previous = os.umask(0o077)
        try:
            executor.write_operator_event(self.record(), 'permission denied')
        finally:
            os.umask(previous)
        files = list(self.parent.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o640)
        executor.os.fchown.assert_called_with(unittest.mock.ANY, 0, self.gid)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_reader_rejects_permissions_schema_links_and_oversized_files(self):
        executor.write_operator_event(self.record())
        path = next(self.parent.iterdir())
        original = path.read_text()
        for mode in (0o644, 0o660, 0o666):
            path.chmod(mode)
            self.assertEqual(executor.read_operator_logs()['status'], 'unavailable')
        path.chmod(0o640)
        for value in [dict(json.loads(original), secret='PRIVATE_SECRET'),
                      dict(json.loads(original), signals=['PRIVATE_SECRET']), ['PRIVATE_SECRET']]:
            path.write_text(json.dumps(value))
            self.assertEqual(executor.read_operator_logs()['events'], [])
        path.write_text('x' * 16385)
        self.assertEqual(executor.read_operator_logs()['status'], 'unavailable')
        path.unlink()
        target = self.state / 'private'
        target.write_text('PRIVATE_SECRET')
        path.symlink_to(target)
        self.assertEqual(executor.read_operator_logs()['status'], 'unavailable')
        self.assertEqual(target.read_text(), 'PRIVATE_SECRET')

    def test_reader_is_bounded_read_only_and_handles_no_access(self):
        for i in range(3):
            executor.write_operator_event(self.record(), 'bwrap')
        with patch.object(bg.Store, 'read', side_effect=AssertionError('private read')), \
                patch.object(executor.subprocess, 'run', side_effect=AssertionError('execution')):
            self.assertEqual(len(executor.read_operator_logs(2)['events']), 2)
            with patch.object(executor, 'protected', side_effect=PermissionError('PRIVATE_SECRET')):
                self.assertNotIn('PRIVATE_SECRET', json.dumps(executor.read_operator_logs()))
        for limit in (0, 201, True):
            with self.assertRaises(ValueError):
                executor.read_operator_logs(limit)

    def test_export_is_optional_and_cannot_mask_main_failure(self):
        self.parent.rmdir()
        executor.write_operator_event(self.record(), 'bwrap')
        self.assertFalse(self.parent.exists())
        with patch.object(executor, 'write_operator_event', side_effect=ValueError('PRIVATE_SECRET')), \
                contextlib.redirect_stderr(io.StringIO()) as out:
            executor.emit_diagnostic('commissioning', 'worker-tools', 'failed', 'sandbox-denied')
        self.assertNotIn('PRIVATE_SECRET', out.getvalue())
        self.assertEqual(executor.read_diagnostics()['commissioning']['status'], 'failed')

    def test_enrollment_creates_dedicated_group_and_is_idempotent(self):
        self.parent.rmdir()
        user = SimpleNamespace(pw_uid=1000, pw_gid=9999)
        with patch.object(admin.pwd, 'getpwnam', return_value=user), \
                patch.object(admin.grp, 'getgrnam', side_effect=[KeyError(), self.group, self.group]), \
                patch.object(admin.os, 'chown'), patch.object(admin.subprocess, 'run') as run:
            admin.grant_log_access('reader')
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ['/usr/sbin/groupadd', '--system', executor.LOG_GROUP],
            ['/usr/sbin/usermod', '--append', '--groups', executor.LOG_GROUP, 'reader']])
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o750)
        self.assertEqual((self.state / 'log-access.json').stat().st_mode & 0o777, 0o600)
        self.group.gr_mem = ['reader']
        with patch.object(admin.pwd, 'getpwnam', return_value=user), patch.object(admin.subprocess, 'run') as run:
            admin.grant_log_access('reader')
        run.assert_not_called()

    def test_enrollment_refuses_unmanaged_group_root_and_service_accounts(self):
        with patch.object(admin.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000, pw_gid=9999)), \
                patch.object(admin.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'unmanaged'):
                admin.grant_log_access('reader')
            for username in ['root', 'mup-bg-worker', '--root', '../reader']:
                with patch.object(admin.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=0)):
                    with self.assertRaises(ValueError):
                        admin.grant_log_access(username)
        run.assert_not_called()

    def test_retained_export_populates_history_without_touching_raw_evidence(self):
        directory = self.state / 'commissioning' / ('b' * 32)
        directory.mkdir(parents=True)
        bg.atomic_json(directory / 'result.json', {'complete': False, 'phases': {'worker-tools': 'failed'}})
        bg.atomic_json(directory / 'worker-tools.jsonl', {'message': 'bwrap: mount: Permission denied PRIVATE_SECRET'})
        bg.atomic_json(directory / 'worker-sandbox.log', {
            'message': 'bwrap: loopback: Failed to create NETLINK_ROUTE socket: Address family not supported by protocol'})
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        admin.export_commissioning_diagnostic('b' * 32)
        event = executor.read_operator_logs()['events'][0]
        self.assertEqual(event['record']['source'], 'retained')
        self.assertEqual(event['signals'], ['bwrap', 'loopback', 'mount', 'netlink-socket',
                                            'permission-denied', 'unsupported-address-family'])
        self.assertEqual(event['record']['classification_hint'], 'sandbox-address-family-denied')
        self.assertEqual(before, {p.name: p.read_bytes() for p in directory.iterdir()})

    def test_logs_cli_needs_neither_root_nor_external_tools(self):
        with patch.object(sys, 'argv', ['background-admin.py', 'logs', '--limit', '3']), \
                patch.object(admin, 'read_operator_logs', return_value={'events': []}) as read, \
                patch.object(bg.Store, 'read', side_effect=AssertionError('private read')), \
                contextlib.redirect_stdout(io.StringIO()):
            admin.main()
        read.assert_called_once_with(3)
        with patch.object(sys, 'argv', ['background-admin.py', 'grant-log-access', '--user', 'reader']):
            with self.assertRaisesRegex(ValueError, 'root-owned installed'):
                admin.main()


class SingleRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = bg.Store(Path(self.temp.name) / 'state')
        self.store.directory.mkdir(mode=0o700)
        self.policy = bg.load_policy()
        state = self.store.read()
        state['paused'] = False
        self.store.save(state)
        self.operator = {'automatic_merge': True}
        self.now = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)  # Thursday, outside schedule.
        now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz)
        patches = [patch.object(executor, 'STATE', self.store.directory),
                   patch.object(executor, 'datetime', Clock), patch.object(bg, 'datetime', Clock)]
        for target, value in {
                'readiness': self.operator, 'resources': {'memory_available_mib': 9000, 'disk_free_gib': 40},
                'service': '', 'refresh_reviews': None, 'report': None, 'pull_requests': []}.items():
            mocked = patch.object(executor, target, return_value=value)
            setattr(self, target, mocked.start())
            self.addCleanup(mocked.stop)
        for mocked in patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(executor, 'develop', side_effect=self.development)
        self.develop = mocked.start()
        self.addCleanup(mocked.stop)

    def development(self, store, state, policy, operator, task, run_id, deadline):
        self.assertFalse(operator['automatic_merge'])
        self.assertEqual(state['active']['mode'], 'test-once')
        self.assertEqual(executor.WORK_DEADLINE, deadline)
        state['tasks'][task['id']] = {'status': 'review', 'pr': 12}
        store.save(state)

    def result(self):
        path, = (self.store.directory / 'trials').glob('*/result.json')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        return json.loads(path.read_text())

    def test_calendar_override_is_one_run_only_and_merge_is_always_off(self):
        executor.run(self.store, self.policy)
        self.service.assert_not_called()
        executor.run(self.store, self.policy, test_once=True)
        self.develop.assert_called_once()
        self.assertTrue(self.operator['automatic_merge'])  # Installed settings are unchanged.
        self.assertFalse(self.refresh_reviews.call_args.args[3]['automatic_merge'])
        self.assertEqual(self.report.call_args.kwargs, {'force': True})
        result = self.result()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['pr'], 12)
        self.assertTrue(result['notification_delivered'])
        for field, minutes in [('work_deadline', 40), ('recovery_deadline', 45)]:
            self.assertEqual(datetime.fromisoformat(result['window'][field]), self.now + timedelta(minutes=minutes))
        self.assertIsNone(executor.WORK_DEADLINE)
        self.assertIsNone(self.store.read()['active'])
        self.service.reset_mock()
        executor.run(self.store, self.policy)
        self.service.assert_not_called()

    def test_non_calendar_safety_gates_remain_blocking(self):
        cases = [({'paused': True}, 'paused'), ({'active': {'id': 'a' * 32}}, 'interrupted-run-needs-recovery'),
                 ({'failures': 3}, 'failure-limit'),
                 ({'tasks': {task['id']: {'status': 'complete'} for task in self.policy['tasks']}}, 'no-ready-task')]
        original = self.store.read()
        for changes, reason in cases:
            with self.subTest(reason=reason):
                self.store.save(dict(original, **changes))
                with self.assertRaisesRegex(ValueError, reason):
                    executor.run(self.store, self.policy, test_once=True)
        self.develop.assert_not_called()
        self.service.assert_not_called()

    def test_scheduled_in_window_behavior_and_empty_queue_review_are_preserved(self):
        real_window = bg.window(datetime(2026, 10, 9, 3, 10, tzinfo=bg.ZoneInfo('Asia/Tokyo')))
        self.develop.side_effect = None
        with patch.object(bg, 'window', return_value=real_window):
            executor.run(self.store, self.policy)
            self.develop.assert_called_once()
            self.assertIs(self.develop.call_args.args[3], self.operator)
            self.assertEqual(self.develop.call_args.args[-1], real_window['work_deadline'])
            self.assertEqual(self.report.call_count, 1)  # No forced single-run report.
            self.assertFalse((self.store.directory / 'trials').exists())
            state = self.store.read()
            state['tasks'] = {task['id']: {'status': 'complete'} for task in self.policy['tasks']}
            self.store.save(state)
            self.develop.reset_mock()
            self.refresh_reviews.reset_mock()
            executor.run(self.store, self.policy)
            self.refresh_reviews.assert_called_once()
            self.develop.assert_not_called()
        self.assertIsNone(executor.WORK_DEADLINE)

    def test_authentication_failure_pauses_and_preserves_private_phase(self):
        self.service.side_effect = ValueError('authentication failed')
        with patch.object(executor, 'emit_diagnostic') as emit:
            with self.assertRaisesRegex(ValueError, 'authentication failed'):
                executor.run(self.store, self.policy, test_once=True)
        self.assertEqual(emit.call_args.args[:3], ('development', 'authentication', 'failed'))
        self.assertIs(emit.call_args.kwargs['error'], self.service.side_effect)
        self.assertEqual(self.result()['phase'], 'authentication')
        self.assertEqual(self.result()['status'], 'failed')
        self.assertTrue(self.store.read()['paused'])
        self.assertTrue(self.store.read()['notification_pending'])
        self.develop.assert_not_called()

    def test_watchdog_retains_interrupted_single_run_until_acknowledgement(self):
        state = self.store.read()
        active = {'id': 'a' * 32, 'task': self.policy['tasks'][0]['id'],
                  'kind': 'development', 'mode': 'test-once'}
        state['active'] = active
        self.store.save(state)
        with patch.object(executor.subprocess, 'run') as stop:
            executor.watchdog(self.store)
        self.assertEqual(stop.call_args.args[0], ['systemctl', 'stop', 'srv6-mup-worker-' + 'a' * 32])
        self.assertEqual(self.store.read()['active'], active)
        self.assertTrue(self.store.read()['paused'])

    def test_resources_live_tasks_and_expired_deadline_are_not_bypassed(self):
        window = {'work_deadline': (self.now + timedelta(minutes=40)).isoformat()}
        for key in ('memory_available_mib', 'disk_free_gib'):
            self.resources.return_value[key] = 0
            decision = executor.run_decision(self.store, self.policy, self.store.read(), window)
            self.assertIn('insufficient-' + key.replace('_', '-'), decision['reasons'])
        self.resources.return_value = {'memory_available_mib': 9000, 'disk_free_gib': 40}
        self.policy['tasks'][0]['profiles'] = ['compact']
        with self.assertRaisesRegex(ValueError, 'test-requires-code-only-task'):
            executor.run(self.store, self.policy, test_once=True)
        decision = executor.run_decision(self.store, self.policy, self.store.read(),
                                         {'work_deadline': self.now.isoformat()})
        self.assertIn('test-deadline-reached', decision['reasons'])
        self.service.assert_not_called()

    def test_open_pr_limit_even_for_resumed_work(self):
        state = self.store.read()
        state['tasks'][self.policy['tasks'][0]['id']] = {'status': 'working'}
        self.store.save(state)
        self.pull_requests.return_value = [{}, {}, {}]
        with self.assertRaisesRegex(ValueError, 'open-pr-limit'):
            executor.run(self.store, self.policy, test_once=True)
        self.develop.assert_not_called()
        self.assertEqual(self.result()['status'], 'blocked')

    def test_fixed_deadline_is_rechecked_after_authentication(self):
        real_decision = executor.run_decision
        def decide(*args, **kwargs):
            result = real_decision(*args, **kwargs)
            if 'open_prs' in kwargs:
                result.update(eligible=False, reasons=['test-deadline-reached'])
            return result
        with patch.object(executor, 'run_decision', side_effect=decide) as decision:
            with self.assertRaisesRegex(ValueError, 'test-deadline-reached'):
                executor.run(self.store, self.policy, test_once=True)
        self.assertEqual(decision.call_args_list[0].args[3], decision.call_args_list[1].args[3])
        self.develop.assert_not_called()

    def test_readiness_and_lock_failure_do_not_start_any_work(self):
        with self.store.locked(), self.assertRaisesRegex(ValueError, 'another background operation'):
            executor.run(self.store, self.policy, test_once=True)
        self.readiness.side_effect = ValueError('not commissioned')
        with self.assertRaisesRegex(ValueError, 'not commissioned'):
            executor.run(self.store, self.policy, test_once=True)
        self.service.assert_not_called()
        self.assertFalse((self.store.directory / 'trials').exists())

    def test_failed_development_is_recorded_and_counts_toward_pause(self):
        state = self.store.read()
        state['failures'] = 2
        self.store.save(state)
        self.develop.side_effect = ValueError('check failed')
        with self.assertRaisesRegex(ValueError, 'check failed'):
            executor.run(self.store, self.policy, test_once=True)
        self.assertEqual(self.result()['status'], 'failed')
        self.assertEqual(self.store.read()['failures'], 3)
        self.assertTrue(self.store.read()['paused'])
        self.assertIsNone(self.store.read()['active'])
        self.assertIsNone(executor.WORK_DEADLINE)

    def test_failed_notification_does_not_claim_complete_or_lose_pr(self):
        self.report.side_effect = [None, ValueError('offline'), ValueError('offline')]
        with self.assertRaisesRegex(ValueError, 'offline'):
            executor.run(self.store, self.policy, test_once=True)
        result = self.result()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['pr'], 12)
        self.assertFalse(result['notification_delivered'])
        self.assertTrue(self.store.read()['notification_pending'])

    def test_tool_error_pauses_immediately_even_with_zero_previous_failures(self):
        self.develop.side_effect = executor.WorkerExecutionError('tool unavailable')
        with self.assertRaisesRegex(ValueError, 'tool unavailable'):
            executor.run(self.store, self.policy, test_once=True)
        self.assertTrue(self.store.read()['paused'])
        self.assertEqual(self.store.read()['failures'], 1)
        self.assertEqual(self.result()['status'], 'failed')

    def test_no_change_outcome_is_not_a_completed_trial(self):
        def no_change(store, state, policy, operator, task, *args):
            state['tasks'][task['id']] = {'status': 'needs-decision'}
        self.develop.side_effect = no_change
        executor.run(self.store, self.policy, test_once=True)
        self.assertEqual(self.result()['status'], 'needs-decision')
        self.assertTrue(self.result()['notification_delivered'])
        self.assertIsNone(self.result()['pr'])

    def test_launcher_uses_fixed_capped_service_without_changing_timers(self):
        original = self.store.path.read_bytes()
        with patch.object(executor.subprocess, 'run') as command:
            executor.launch_test_once(self.store, self.policy)
        argv = command.call_args.args[0]
        self.assertEqual(argv[0], 'systemd-run')
        for value in ('--unit=srv6-mup-background-test.service', 'RuntimeMaxSec=45min',
                      'UMask=0077', 'ProtectSystem=strict', 'KillMode=control-group',
                      'TimeoutStopSec=60', '_execute-test-once'):
            self.assertIn(value, argv)
        self.assertNotIn('--wait', argv)
        self.assertFalse(any('.timer' in value for value in argv))
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertTrue(command.call_args.kwargs['check'])

    def test_launcher_refuses_paused_queue_and_propagates_duplicate_unit_failure(self):
        with patch.object(executor.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'systemd-run')):
            with self.assertRaises(subprocess.CalledProcessError):
                executor.launch_test_once(self.store, self.policy)
        state = self.store.read()
        state['paused'] = True
        self.store.save(state)
        with patch.object(executor.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'paused'):
                executor.launch_test_once(self.store, self.policy)
            command.assert_not_called()

    def test_admin_entrypoints_refuse_ordinary_checkout(self):
        for action in ('test-once', '_execute-test-once'):
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/background-admin.py'), action],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('root-owned installed administration tool', result.stderr)


class SingleRunNotificationTests(unittest.TestCase):
    def test_final_trial_report_bypasses_weekly_deduplication_without_raw_output(self):
        with tempfile.TemporaryDirectory() as root:
            store = bg.Store(root)
            state = store.read()
            state['report_issue'] = 8
            state['last_result'] = 'PRIVATE LOG MUST NOT BE PUBLISHED'
            policy = bg.load_policy()
            with patch.object(executor, 'github', return_value={}) as github:
                executor.report(store, state, policy)
                executor.report(store, state, policy)
                self.assertEqual(github.call_count, 1)
                executor.report(store, state, policy, force=True)
                self.assertEqual(github.call_count, 2)
                args = github.call_args.args
                self.assertTrue(args[1].endswith('/issues/8'))
                self.assertEqual(args[2], 'PATCH')
                self.assertIn('Single-run test result.', args[3]['body'])
                self.assertNotIn('PRIVATE LOG', args[3]['body'])


class IsolationProbeTests(unittest.TestCase):
    def test_existing_masked_directory_is_denied(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'masked'
            path.mkdir(mode=0o000)
            try:
                self.assertTrue(path.exists())
                self.assertTrue(admin.directory_denied(path))
            finally:
                path.chmod(0o700)
            self.assertFalse(admin.directory_denied(path))

    def test_masked_socket_denied_but_live_socket_rejected(self):
        with tempfile.TemporaryDirectory() as root, socket.socket(socket.AF_UNIX) as listener:
            path = Path(root) / 'test.sock'
            listener.bind(str(path))
            listener.listen(1)
            path.chmod(0o000)
            try:
                self.assertTrue(path.exists())
                self.assertTrue(admin.socket_denied(str(path)))
            finally:
                path.chmod(0o600)
            self.assertFalse(admin.socket_denied(str(path)))

    def test_only_permission_denial_or_absent_socket_is_accepted(self):
        for result in (0, errno.ECONNREFUSED, errno.ETIMEDOUT, errno.EIO,
                       errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR):
            with self.subTest(result=result), patch.object(admin.socket, 'socket') as mocked:
                mocked.return_value.__enter__.return_value.connect_ex.return_value = result
                self.assertEqual(admin.socket_denied('/test.sock'), result in (
                    errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR))

    def test_probe_keeps_network_gate_strict_and_names_failure(self):
        for result in (errno.EACCES, errno.EPERM, errno.ECONNREFUSED, 0, errno.ETIMEDOUT,
                       errno.EAGAIN, errno.EHOSTUNREACH, errno.ENETUNREACH, None):
            with self.subTest(result=result), patch.object(admin.os, 'getuid', return_value=1000), \
                    patch.object(admin.os, 'access', return_value=False), \
                    patch.object(admin, 'directory_denied', return_value=True), \
                    patch.object(admin, 'socket_denied', return_value=True), \
                    patch.object(admin.socket, 'socket') as mocked, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                connection = mocked.return_value.__enter__.return_value
                if result is None:
                    connection.sendto.side_effect = TimeoutError('timed out')
                elif result:
                    connection.sendto.side_effect = OSError(result, 'test failure')
                if result in (errno.EACCES, errno.EPERM):
                    admin.isolation_probe()
                else:
                    with self.assertRaisesRegex(ValueError, 'private_network_denied'):
                        admin.isolation_probe()
                self.assertEqual(json.loads(output.getvalue())['network_errno'], result)
                self.assertEqual(json.loads(output.getvalue())['network_probe'], 'udp-self-send')
                mocked.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
                connection.connect_ex.assert_not_called()

    def test_udp_probe_owns_its_loopback_destination_and_success_is_not_denial(self):
        with patch.object(admin.socket, 'socket') as mocked:
            connection = mocked.return_value.__enter__.return_value
            connection.getsockname.return_value = ('127.0.0.2', 49152)
            self.assertEqual(admin.private_network_errno(), 0)
            connection.bind.assert_called_once_with(('127.0.0.2', 0))
            connection.settimeout.assert_called_once_with(2)
            connection.sendto.assert_called_once_with(b'srv6-mup-isolation-probe', ('127.0.0.2', 49152))
            connection.recv.assert_not_called()
            mocked.return_value.__exit__.assert_called_once()

    def test_udp_socket_setup_failure_is_not_egress_denial(self):
        with patch.object(admin.socket, 'socket', side_effect=PermissionError(errno.EPERM, 'test')):
            with self.assertRaises(PermissionError):
                admin.private_network_errno()
        with patch.object(admin.socket, 'socket') as mocked:
            connection = mocked.return_value.__enter__.return_value
            connection.bind.side_effect = PermissionError(errno.EACCES, 'test')
            with self.assertRaises(PermissionError):
                admin.private_network_errno()
            connection.sendto.assert_not_called()


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = self.root / 'worker'
        self.worker.mkdir(mode=0o700)
        mocked = patch.object(executor, 'HOMES', {'worker': self.worker})
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(admin.pwd, 'getpwnam', return_value=admin.pwd.getpwuid(os.getuid()))
        mocked.start()
        self.addCleanup(mocked.stop)
        self.install = self.root / 'install'
        self.install.mkdir()
        self.store = bg.Store(self.root / 'state')
        self.store.directory.mkdir(mode=0o700)
        self.state = self.store.read()
        self.state['paused'] = False
        bg.atomic_json(self.install / 'operator.json', {
            'commissioned': True, 'automatic_merge': True, 'model': 'retained-model'})
        self.files = ['Makefile', 'scripts/example.py', 'docs/nested/guide.md', 'config/public-source.json']
        for name in self.files:
            target = self.install / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('public fixture\n')
        (self.install / 'scripts/example.py').chmod(0o755)
        (self.install / 'config/public-source.json').write_text(json.dumps({'files': self.files}))
        (self.install / 'candidates').mkdir()
        for name in ['bin/codex', 'venv/lib/tool.py', 'go/README.md']:
            target = self.install / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('not public source\n')
        bg.atomic_json(self.install / 'installation.json', {
            'files': {name: bg.digest(self.install / name) for name in self.files + ['bin/codex']}})
        mocked = patch.object(admin, 'protected')
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(admin, 'verify_codex_bundle')
        mocked.start()
        self.addCleanup(mocked.stop)
        for attribute, value in [('INSTALL', self.install), ('STATE', self.store.directory)]:
            mocked = patch.object(admin, attribute, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def service(self, role, command, **kwargs):
        Path(kwargs['output_file']).write_text('private diagnostic output\n')
        if kwargs.get('json_events'):
            challenge = re.search(r'[0-9a-f]{32}', kwargs['input_text']).group()
            (kwargs['cwd'] / 'tool-probe.txt').write_text(challenge)

    def test_every_phase_has_retained_evidence_and_success_remains_paused(self):
        with patch.object(admin, 'service', side_effect=self.service) as service:
            admin.commission(self.store, self.state, 'Example', 'example@example.invalid')
        self.assertEqual(service.call_count, 7)
        report = next((self.store.directory / 'commissioning').glob('*/result.json'))
        self.assertTrue(json.loads(report.read_text())['complete'])
        self.assertEqual(json.loads(report.read_text())['phases']['worker-tools'], 'passed')
        self.assertTrue((report.parent / 'worker-tools.jsonl').exists())
        source_tree = Path(json.loads(report.read_text())['source_tree'])
        self.assertEqual(service.call_args.kwargs['cwd'], source_tree)
        self.assertEqual(service.call_args.args[0], 'checks')
        self.assertEqual(service.call_args.args[1], ['make', 'check'])
        self.assertEqual(source_tree.parent, self.install / 'candidates')
        self.assertFalse((source_tree / 'operator.json').exists())
        self.assertEqual(len(list(report.parent.glob('*.log'))), 6)
        sandbox = service.call_args_list[4]
        self.assertEqual(sandbox.args[0], 'worker')
        self.assertEqual(sandbox.args[1][1:], ['--unshare-user', '--unshare-net', '--unshare-pid',
                                              '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev', '--', '/bin/true'])
        self.assertEqual(sandbox.kwargs['seconds'], 30)
        self.assertEqual(sandbox.kwargs['output_file'], report.parent / 'worker-sandbox.log')
        self.assertIn('exec', service.call_args_list[5].args[1])
        workspace = Path(json.loads(report.read_text())['worker_workspace'])
        self.assertEqual(workspace.parent, self.worker / 'work')
        self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
        self.assertEqual(workspace.stat().st_uid, os.getuid())
        self.assertEqual(service.call_args_list[3].kwargs['cwd'], workspace)
        self.assertEqual(service.call_args_list[3].args[0], 'worker')
        self.assertIn('probe-workspace', service.call_args_list[3].args[1])
        self.assertTrue(self.store.read()['paused'])
        operator = json.loads((self.install / 'operator.json').read_text())
        self.assertTrue(operator['commissioned'])
        self.assertFalse(operator['automatic_merge'])
        self.assertEqual(operator['model'], 'retained-model')

    def test_failed_probe_revokes_previous_acceptance_and_retains_log(self):
        def fail(role, command, **kwargs):
            self.service(role, command, **kwargs)
            if 'probe-isolation' in command:
                raise ValueError('probe failed')
        with patch.object(admin, 'service', side_effect=fail) as service, \
                patch.object(admin, 'emit_diagnostic') as emit:
            with self.assertRaisesRegex(ValueError, 'isolation-probe failed; inspect private log'):
                admin.commission(self.store, self.state, 'Example', 'example@example.invalid')
        self.assertEqual(service.call_count, 3)
        report = next((self.store.directory / 'commissioning').glob('*/result.json'))
        self.assertEqual(emit.call_args.args, ('commissioning', 'isolation-probe', 'failed'))
        self.assertEqual(emit.call_args.kwargs['logs'][0], report.parent / 'isolation-probe.log')
        self.assertEqual(json.loads(report.read_text())['phases']['isolation-probe'], 'failed')
        self.assertTrue((report.parent / 'isolation-probe.log').exists())
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])
        self.assertTrue(self.store.read()['paused'])
        self.assertEqual(list((self.install / 'candidates').iterdir()), [])

    def test_source_snapshot_is_public_only_and_readable_under_private_umask(self):
        destination = self.install / 'candidates' / 'test-snapshot'
        previous = os.umask(0o077)
        try:
            admin.commissioning_source(destination)
        finally:
            os.umask(previous)
        actual = {str(path.relative_to(destination)) for path in destination.rglob('*') if path.is_file()}
        self.assertEqual(actual, set(self.files))
        for path in [destination, *destination.rglob('*')]:
            if path.is_dir() or path.name == 'example.py':
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            else:
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            if path.is_file():
                self.assertEqual(path.read_bytes(), (self.install / path.relative_to(destination)).read_bytes())

    def test_source_snapshot_refuses_overwrite_or_modified_inventory(self):
        destination = self.install / 'candidates' / 'retained'
        destination.mkdir()
        with self.assertRaises(FileExistsError):
            admin.commissioning_source(destination)
        (self.install / 'config/public-source.json').write_text(json.dumps({'files': ['operator.json']}))
        destination = self.install / 'candidates' / 'new'
        with self.assertRaisesRegex(ValueError, 'publication inventory changed'):
            admin.commissioning_source(destination)
        self.assertFalse(destination.exists())

    def test_failed_source_preparation_retains_evidence_without_executing_checks(self):
        (self.install / 'Makefile').write_text('modified installed source\n')
        with patch.object(admin, 'service', side_effect=self.service) as service:
            with self.assertRaisesRegex(ValueError, 'source-checks failed; inspect private log'):
                admin.commission(self.store, self.state, 'Example', 'example@example.invalid')
        self.assertEqual(service.call_count, 6)
        report = next((self.store.directory / 'commissioning').glob('*/result.json'))
        result = json.loads(report.read_text())
        self.assertEqual(result['phases']['source-checks'], 'failed')
        self.assertFalse(result['complete'])
        self.assertTrue(Path(result['source_tree']).exists())
        log = report.parent / 'source-checks.log'
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertIn('differs from installed manifest', log.read_text())
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])
        self.assertTrue(self.store.read()['paused'])


    def test_workspace_failure_blocks_commissioning_and_retains_evidence(self):
        def fail(role, command, **kwargs):
            self.service(role, command, **kwargs)
            if 'probe-workspace' in command:
                raise ValueError('workspace failed')
        with patch.object(admin, 'service', side_effect=fail) as service:
            with self.assertRaisesRegex(ValueError, 'worker-workspace failed; inspect private log'):
                admin.commission(self.store, self.state, 'Example', 'example@example.invalid')
        self.assertEqual(service.call_count, 4)
        report = next((self.store.directory / 'commissioning').glob('*/result.json'))
        result = json.loads(report.read_text())
        self.assertEqual(result['phases']['worker-workspace'], 'failed')
        self.assertNotIn('source-checks', result['phases'])
        self.assertTrue(Path(result['worker_workspace']).is_dir())
        self.assertTrue((report.parent / 'worker-workspace.log').is_file())
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])
        self.assertTrue(self.store.read()['paused'])

    def test_tool_probe_requires_real_artifact_not_successful_exit_or_claim(self):
        def no_artifact(role, command, **kwargs):
            Path(kwargs['output_file']).write_text('claims success, but created nothing\n')
        with patch.object(admin, 'service', side_effect=no_artifact):
            with self.assertRaisesRegex(ValueError, 'worker-tools failed'):
                admin.commission(self.store, self.state, 'Example', 'example@example.invalid')
        result = json.loads(next((self.store.directory / 'commissioning').glob('*/result.json')).read_text())
        self.assertEqual(result['phases']['worker-tools'], 'failed')
        self.assertNotIn('source-checks', result['phases'])
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])

    def test_missing_bundle_blocks_tool_probe_and_commissioning(self):
        with patch.object(admin, 'verify_codex_bundle', side_effect=ValueError('missing bundle')), \
                patch.object(admin, 'service', side_effect=self.service) as service:
            with self.assertRaisesRegex(ValueError, 'worker-tools failed'):
                admin.commission(self.store, self.state, 'Example', 'example@example.invalid')
        self.assertEqual(service.call_count, 4)
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])

    def test_sandbox_failure_prevents_model_call_and_exports_probe_evidence(self):
        def fail(role, command, **kwargs):
            self.service(role, command, **kwargs)
            if '--unshare-net' in command:
                raise ValueError('sandbox probe failed')
        with patch.object(admin, 'service', side_effect=fail) as service, \
                patch.object(admin, 'emit_diagnostic') as emit:
            with self.assertRaisesRegex(ValueError, 'worker-tools failed'):
                admin.commission(self.store, self.state, 'Example', 'example@example.invalid')
        self.assertEqual(service.call_count, 5)
        self.assertFalse(any('exec' in call.args[1] for call in service.call_args_list))
        report = next((self.store.directory / 'commissioning').glob('*/result.json'))
        self.assertIn(report.parent / 'worker-sandbox.log', emit.call_args.kwargs['logs'])
        self.assertEqual(json.loads(report.read_text())['phases']['worker-tools'], 'failed')
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])
        self.assertTrue(self.store.read()['paused'])

    def test_tool_probe_refuses_wrong_contents_and_symlinks(self):
        workspace = self.worker / 'probe'
        workspace.mkdir()
        evidence = self.root / 'evidence'
        evidence.mkdir()
        def wrong(role, argv, **kwargs):
            (workspace / 'tool-probe.txt').write_text('wrong')
        with patch.object(admin, 'service', side_effect=wrong):
            with self.assertRaisesRegex(ValueError, 'expected private artifact'):
                admin.commissioning_tools(workspace, evidence, 'retained-model')
        (workspace / 'tool-probe.txt').unlink()
        (workspace / 'tool-probe.txt').symlink_to(self.install / 'operator.json')
        with patch.object(admin, 'service'):
            with self.assertRaises(OSError):
                admin.commissioning_tools(workspace, evidence, 'retained-model')


class WorkerDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'worker'
        self.home.mkdir(mode=0o700)
        mocked = patch.object(executor, 'HOMES', {'worker': self.home})
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_new_parent_is_searchable_even_under_private_umask(self):
        previous = os.umask(0o077)
        try:
            parent = executor.worker_work_parent()
        finally:
            os.umask(previous)
        self.assertEqual(parent, self.home / 'work')
        self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)

    def test_legacy_parent_repaired_without_changing_checkout_or_credentials(self):
        parent = self.home / 'work'
        parent.mkdir(mode=0o700)
        checkout = parent / 'existing'
        checkout.mkdir(mode=0o700)
        edited = checkout / 'unfinished.txt'
        edited.write_text('preserve edits')
        edited.chmod(0o600)
        credentials = self.home / '.codex'
        credentials.mkdir(mode=0o700)
        executor.worker_work_parent()
        self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(checkout.stat().st_mode & 0o777, 0o700)
        self.assertEqual(edited.stat().st_mode & 0o777, 0o600)
        self.assertEqual(edited.read_text(), 'preserve edits')
        self.assertEqual(credentials.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o700)

    def test_symlink_parent_is_refused_without_changing_target(self):
        target = Path(self.temp.name) / 'unrelated'
        target.mkdir(mode=0o700)
        (self.home / 'work').symlink_to(target)
        with self.assertRaises(OSError):
            executor.worker_work_parent()
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_unexpected_owner_or_writable_parent_is_refused(self):
        parent = self.home / 'work'
        parent.mkdir(mode=0o700)
        with patch.object(executor.os, 'fstat', return_value=SimpleNamespace(
                st_uid=os.geteuid() + 1, st_mode=parent.stat().st_mode)):
            with self.assertRaisesRegex(ValueError, 'ownership or permissions'):
                executor.worker_work_parent()
        self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
        for mode in (0o777, 0o775):
            parent.chmod(mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'ownership or permissions'):
                executor.worker_work_parent()
            self.assertEqual(parent.stat().st_mode & 0o7777, mode)

    def test_special_mode_parent_is_refused_without_setting_privileged_bits(self):
        parent = self.home / 'work'
        parent.mkdir(mode=0o700)
        info = parent.stat()
        # RestrictSUIDSGID forbids creating this fixture on disk. Supply only
        # observed metadata; still exercise the real rejection before chmod.
        for special in (0o1000, 0o2000, 0o4000):
            with self.subTest(special=special), \
                    patch.object(executor.os, 'fstat', return_value=SimpleNamespace(
                        st_uid=info.st_uid, st_mode=info.st_mode | special)), \
                    patch.object(executor.os, 'fchmod') as chmod:
                with self.assertRaisesRegex(ValueError, 'ownership or permissions'):
                    executor.worker_work_parent()
                chmod.assert_not_called()
            self.assertEqual(parent.stat().st_mode & 0o7777, 0o700)

    def test_workspace_probe_checks_parent_protection_and_scratch_io(self):
        for writable_parent in (False, True):
            with self.subTest(writable_parent=writable_parent), \
                    patch.object(admin.os, 'geteuid', return_value=1000), \
                    patch.object(admin.os, 'access', side_effect=lambda path, mode:
                                 writable_parent if path == '..' and mode == os.W_OK else True), \
                    patch.object(admin.tempfile, 'TemporaryFile', return_value=io.BytesIO()) as temporary, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                if writable_parent:
                    with self.assertRaisesRegex(ValueError, 'parent_not_writable'):
                        admin.workspace_probe()
                else:
                    admin.workspace_probe()
                temporary.assert_called_once_with(dir='.')
                self.assertTrue(json.loads(output.getvalue())['checks']['workspace_io'])


class CoordinatorUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.install = self.root / 'source', self.root / 'install'
        self.store = bg.Store(self.root / 'state')
        self.store.directory.mkdir(mode=0o700)
        self.files = ['scripts/background-admin.py', 'config/background-development.yml', 'config/public-source.json']
        for directory in (self.source, self.install):
            (directory / 'scripts').mkdir(parents=True)
            (directory / 'config').mkdir()
            (directory / 'scripts/background-admin.py').write_text('old source\n')
            (directory / 'config/background-development.yml').write_text('unchanged policy\n')
            (directory / 'config/public-source.json').write_text(json.dumps({'files': self.files}))
        (self.install / 'bin').mkdir()
        (self.install / 'bin/codex').write_text('retained tool\n')
        (self.install / 'review-schema.json').write_text('{}')
        manifest = {'version': 1, 'files': {name: bg.digest(self.install / name)
                    for name in self.files + ['bin/codex', 'review-schema.json']}}
        bg.atomic_json(self.install / 'installation.json', manifest)
        bg.atomic_json(self.install / 'operator.json', {
            'commissioned': True, 'automatic_merge': True, 'model': 'retained-model'})
        state = self.store.read()
        state['tasks'] = {'example': {'status': 'queued'}}
        self.store.save(state)
        (self.source / 'scripts/background-admin.py').write_text('fixed source\n')
        for attribute, value in [('ROOT', self.source), ('INSTALL', self.install), ('STATE', self.store.directory)]:
            mocked = patch.object(installer, attribute, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        protected = patch.object(installer, 'protected')
        protected.start()
        self.addCleanup(protected.stop)
        command = patch.object(installer.subprocess, 'check_output', side_effect=lambda argv, **kwargs:
                               'disabled\n' if '--property=UnitFileState' in argv else 'inactive\n')
        self.command = command.start()
        self.addCleanup(command.stop)

    def test_repair_preserves_backup_tools_tasks_and_requires_recommission(self):
        with patch.object(installer.subprocess, 'run') as run:
            installer.update_coordinator()
            run.assert_not_called()  # No apt, user creation, login or service start.
        self.assertEqual((self.install / self.files[0]).read_text(), 'fixed source\n')
        report = next((self.store.directory / 'updates').glob('*/installation.json'))
        self.assertEqual((report.parent / self.files[0]).read_text(), 'old source\n')
        manifest = json.loads((self.install / 'installation.json').read_text())
        for name, digest in manifest['files'].items():
            self.assertEqual(bg.digest(self.install / name), digest)
        operator = json.loads((self.install / 'operator.json').read_text())
        self.assertEqual(operator['model'], 'retained-model')
        self.assertFalse(operator['commissioned'])
        self.assertFalse(operator['automatic_merge'])
        self.assertTrue(self.store.read()['paused'])
        self.assertEqual(self.store.read()['tasks'], {'example': {'status': 'queued'}})

    def package_fixture(self):
        package = self.root / 'package'
        pins = {}
        for name in executor.CODEX_FILES:
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((self.install / 'bin/codex').read_bytes() if name == 'bin/codex' else name.encode())
            pins[name] = bg.digest(path)
        return package, pins

    def test_explicit_package_repair_adds_only_pinned_companions_and_retains_main_tool(self):
        package, pins = self.package_fixture()
        original = (self.install / 'bin/codex').read_bytes()
        with patch.object(installer, 'CODEX_FILES', pins):
            installer.update_coordinator(package)
            installer.update_coordinator(package)  # Complete repaired manifests remain supported.
        manifest = json.loads((self.install / 'installation.json').read_text())['files']
        self.assertEqual(set(manifest), set(self.files) | set(pins) | {'review-schema.json'})
        for name, expected in pins.items():
            self.assertEqual(bg.digest(self.install / name), expected)
            if name != 'bin/codex':
                self.assertEqual((self.install / name).stat().st_mode & 0o777,
                                 0o644 if name == 'codex-package.json' else 0o755)
        self.assertEqual((self.install / 'bin/codex').read_bytes(), original)
        added = json.loads(next((self.store.directory / 'updates').glob('*/added-files.json')).read_text())
        self.assertEqual(set(added), set(pins) - {'bin/codex'})
        self.assertTrue(self.store.read()['paused'])
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])

    def test_wrong_package_and_untracked_helper_refused_before_state_changes(self):
        package, pins = self.package_fixture()
        helper = package / 'bin/codex-code-mode-host'
        original = helper.read_bytes()
        helper.write_text('wrong version')
        with patch.object(installer, 'CODEX_FILES', pins):
            with self.assertRaisesRegex(ValueError, 'reviewed 0.154.0'):
                installer.update_coordinator(package)
            helper.write_bytes(original)
            (self.install / 'bin/codex-code-mode-host').write_text('untracked retained file')
            with self.assertRaisesRegex(ValueError, 'untracked package entry retained'):
                installer.update_coordinator(package)
        self.assertFalse((self.store.directory / 'updates').exists())
        self.assertTrue(json.loads((self.install / 'operator.json').read_text())['commissioned'])

    def test_package_repair_cannot_replace_different_installed_codex(self):
        package, pins = self.package_fixture()
        (package / 'bin/codex').write_text('other tool')
        pins['bin/codex'] = bg.digest(package / 'bin/codex')
        with patch.object(installer, 'CODEX_FILES', pins):
            with self.assertRaisesRegex(ValueError, 'cannot replace the installed Codex'):
                installer.update_coordinator(package)
        self.assertFalse((self.store.directory / 'updates').exists())

    def test_package_links_are_rejected_even_if_bytes_match(self):
        package, pins = self.package_fixture()
        helper = package / 'bin/codex-code-mode-host'
        target = self.root / 'outside'
        helper.rename(target)
        helper.symlink_to(target)
        with patch.object(installer, 'CODEX_FILES', pins):
            with self.assertRaisesRegex(ValueError, 'plain files'):
                installer.codex_package_payloads(package)

    def test_partial_package_write_retains_evidence_and_revokes_commissioning(self):
        package, pins = self.package_fixture()
        original_manifest = (self.install / 'installation.json').read_bytes()
        real_open = installer.os.open
        def fail(path, *args, **kwargs):
            if Path(path) == self.install / 'codex-path/rg':
                raise OSError('simulated package write failure')
            return real_open(path, *args, **kwargs)
        with patch.object(installer, 'CODEX_FILES', pins), patch.object(installer.os, 'open', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'package write failure'):
                installer.update_coordinator(package)
        self.assertEqual((self.install / 'installation.json').read_bytes(), original_manifest)
        self.assertTrue((self.install / 'bin/codex-code-mode-host').is_file())
        self.assertTrue(list((self.store.directory / 'updates').glob('*/added-files.json')))
        self.assertTrue(self.store.read()['paused'])
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])

    def test_readiness_requires_all_pinned_package_entries_and_hashes(self):
        package, pins = self.package_fixture()
        with patch.object(installer, 'CODEX_FILES', pins):
            installer.update_coordinator(package)
        with patch.object(executor, 'INSTALL', self.install), patch.object(executor, 'CODEX_FILES', pins), \
                patch.object(executor, 'protected'):
            executor.verify_codex_bundle()
            (self.install / 'bin/codex-code-mode-host').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'missing or changed'):
                executor.verify_codex_bundle()

    def test_changed_installed_file_refused_before_writes(self):
        (self.install / self.files[0]).write_text('unexpected edit\n')
        with self.assertRaisesRegex(ValueError, 'differ from their manifest'):
            installer.update_coordinator()
        self.assertFalse((self.store.directory / 'updates').exists())

    def test_active_single_run_blocks_coordinator_update(self):
        self.command.side_effect = lambda argv, **kwargs: (
            'active\n' if executor.TEST_UNIT in argv else
            'disabled\n' if '--property=UnitFileState' in argv else 'inactive\n')
        with self.assertRaisesRegex(ValueError, 'stop'):
            installer.update_coordinator()
        self.assertFalse((self.store.directory / 'updates').exists())

    def test_policy_changes_refused_before_writes(self):
        (self.source / self.files[1]).write_text('unapproved policy\n')
        with self.assertRaisesRegex(ValueError, 'cannot change policy'):
            installer.update_coordinator()
        self.assertEqual((self.install / self.files[0]).read_text(), 'old source\n')

    def test_inventory_expansion_refused(self):
        (self.source / self.files[2]).write_text(json.dumps({'files': self.files + ['new.py']}))
        with self.assertRaisesRegex(ValueError, 'cannot add/remove inventory'):
            installer.update_coordinator()

    def test_active_timer_refused(self):
        self.command.side_effect = lambda argv, **kwargs: 'active\n'
        with self.assertRaisesRegex(ValueError, 'stop background services'):
            installer.update_coordinator()

    def test_enabled_timer_refused(self):
        self.command.side_effect = lambda argv, **kwargs: 'enabled\n' if '--property=UnitFileState' in argv else 'inactive\n'
        with self.assertRaisesRegex(ValueError, 'disable both timers'):
            installer.update_coordinator()

    def test_interrupted_run_refused(self):
        state = self.store.read()
        state['active'] = {'id': 'a' * 32}
        self.store.save(state)
        with self.assertRaisesRegex(ValueError, 'recover interrupted work'):
            installer.update_coordinator()

    def test_partial_update_retains_backup_and_cannot_remain_commissioned(self):
        replace = os.replace
        def fail(source, destination):
            if Path(destination) == self.install / self.files[0]:
                raise OSError('simulated update failure')
            return replace(source, destination)
        with patch.object(installer.os, 'replace', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'simulated update failure'):
                installer.update_coordinator()
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])
        self.assertTrue(list((self.store.directory / 'updates').glob('*/installation.json')))
        self.assertTrue(self.store.read()['paused'])

    def go_fixture(self):
        for name in sorted(installer.GO_REPAIR_PATHS):
            if name == 'config/versions.lock.yml':
                payload = ('toolchains:\n  go:\n    version: 1.26.8\n    platform: linux-amd64\n'
                           '    url: https://go.dev/dl/go1.26.8.linux-amd64.tar.gz\n'
                           f'    sha256: {installer.GO_PREVIOUS_SHA256}\n').encode()
            else:
                payload = b'fixture 1.26.8\n'
            for directory in (self.install, self.source):
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload if directory == self.install else installer.go_repair_bytes(payload))
            self.files.append(name)
        for directory in (self.install, self.source):
            (directory / 'config/public-source.json').write_text(json.dumps({'files': self.files}))
        manifest = json.loads((self.install / 'installation.json').read_text())
        manifest['files'].update({name: bg.digest(self.install / name) for name in self.files})
        bg.atomic_json(self.install / 'installation.json', manifest)
        (self.install / 'go/bin').mkdir(parents=True)
        (self.install / 'go/VERSION').write_text('go1.26.8\n')
        (self.install / 'go/bin/go').write_text('old SDK; never executed')
        (self.install / 'bin/go').symlink_to('../go/bin/go')
        archive = self.root / 'go.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for name, data in [('go/VERSION', b'go1.26.9\n'), ('go/bin/go', b'new SDK; never executed')]:
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o755 if name.endswith('/go') else 0o644
                output.addfile(member, io.BytesIO(data))
        # Pin validation is tested separately. Keep the production hash in the
        # source fixture; replace only the archive reader for lifecycle tests.
        return archive

    def test_explicit_go_repair_preserves_sdk_and_recommission_gate(self):
        archive = self.go_fixture()
        with patch.object(installer, 'go_archive_payload', return_value=archive.read_bytes()), \
                patch.object(installer.subprocess, 'run') as run:
            installer.update_coordinator(go_archive=archive)
            run.assert_not_called()
        self.assertEqual((self.install / 'go/VERSION').read_text(), 'go1.26.9\n')
        self.assertEqual(os.readlink(self.install / 'bin/go'), '../go/bin/go')
        backup = next((self.store.directory / 'updates').glob('*/go-previous'))
        self.assertEqual((backup / 'VERSION').read_text(), 'go1.26.8\n')
        manifest = json.loads((self.install / 'installation.json').read_text())
        self.assertEqual(manifest['go_archive']['sha256'], installer.GO_REPAIR_SHA256)
        for name, expected in manifest['files'].items():
            self.assertEqual(bg.digest(self.install / name), expected)
        self.assertTrue(self.store.read()['paused'])
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])
        with patch.object(installer, 'go_archive_payload', return_value=archive.read_bytes()):
            installer.update_coordinator(go_archive=archive)
        self.assertEqual(len(list((self.store.directory / 'updates').glob('*/go-previous'))), 2)

    def test_go_pin_changes_still_require_explicit_archive(self):
        self.go_fixture()
        with self.assertRaisesRegex(ValueError, 'cannot change policy'):
            installer.update_coordinator()
        self.assertFalse((self.store.directory / 'updates').exists())

    def test_go_repair_rejects_nonmechanical_and_unrelated_policy_changes(self):
        archive = self.go_fixture()
        for name in ['config/supply-chain-policy.yml', 'config/background-development.yml']:
            with self.subTest(name=name):
                path = self.source / name
                original = path.read_bytes()
                path.write_bytes(original + b'changed policy\n')
                with patch.object(installer, 'go_archive_payload', return_value=archive.read_bytes()):
                    with self.assertRaisesRegex(ValueError, 'exact reviewed|cannot change policy'):
                        installer.update_coordinator(go_archive=archive)
                path.write_bytes(original)
        self.assertFalse((self.store.directory / 'updates').exists())

    def test_go_archive_wrong_digest_and_symlink_refused(self):
        archive = self.go_fixture()
        with self.assertRaisesRegex(ValueError, 'upstream SHA-256'):
            installer.update_coordinator(go_archive=archive)
        link = self.root / 'linked.tar.gz'
        link.symlink_to(archive)
        with self.assertRaises(OSError):
            installer.go_archive_payload(link)
        self.assertFalse((self.store.directory / 'updates').exists())

    def test_go_archive_verified_bytes_are_returned_without_execution(self):
        archive = self.go_fixture()
        with patch.object(installer, 'GO_REPAIR_SHA256', bg.digest(archive)), \
                patch.object(installer.subprocess, 'run') as run:
            self.assertEqual(installer.go_archive_payload(archive), archive.read_bytes())
            run.assert_not_called()

    def test_go_archive_rejects_traversal_links_duplicates_and_special_members(self):
        cases = [('go/../escape', tarfile.REGTYPE), ('go/link', tarfile.SYMTYPE),
                 ('go/hardlink', tarfile.LNKTYPE), ('go/device', tarfile.CHRTYPE),
                 ('other/file', tarfile.REGTYPE), ('go/duplicate', tarfile.REGTYPE)]
        for index, (name, kind) in enumerate(cases):
            with self.subTest(name=name):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w:gz') as archive:
                    member = tarfile.TarInfo(name)
                    member.type, member.linkname = kind, '/outside'
                    archive.addfile(member)
                    if name == 'go/duplicate':
                        archive.addfile(member)
                with self.assertRaises(ValueError):
                    installer.stage_go_archive(stream.getvalue(), self.root / f'stage-{index}')

    def test_go_repair_rejects_unexpected_launcher(self):
        archive = self.go_fixture()
        (self.install / 'bin/go').unlink()
        (self.install / 'bin/go').symlink_to('/other/go')
        with patch.object(installer, 'go_archive_payload', return_value=archive.read_bytes()):
            with self.assertRaisesRegex(ValueError, 'expected SDK link'):
                installer.update_coordinator(go_archive=archive)
        self.assertFalse((self.store.directory / 'updates').exists())

    def test_go_swap_failure_retains_old_sdk_manifest_and_paused_state(self):
        archive = self.go_fixture()
        original_manifest = (self.install / 'installation.json').read_bytes()
        rename = os.rename
        def fail(source, destination):
            if Path(destination) == self.install / 'go':
                raise OSError('simulated SDK swap failure')
            return rename(source, destination)
        with patch.object(installer, 'go_archive_payload', return_value=archive.read_bytes()), \
                patch.object(installer.os, 'rename', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'SDK swap failure'):
                installer.update_coordinator(go_archive=archive)
        self.assertEqual((self.install / 'installation.json').read_bytes(), original_manifest)
        self.assertTrue(list((self.store.directory / 'updates').glob('*/go-previous/VERSION')))
        self.assertTrue(list((self.store.directory / 'updates').glob('*/go-staging/go/VERSION')))
        self.assertTrue(self.store.read()['paused'])
        self.assertFalse(json.loads((self.install / 'operator.json').read_text())['commissioned'])


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


class WorkerEventTests(unittest.TestCase):
    EVENTS = [{'type': 'thread.started', 'thread_id': 'fixture'}, {'type': 'turn.started'},
              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Done'}},
              {'type': 'turn.completed', 'usage': {}}]

    def test_netlink_is_worker_only_without_relaxing_other_boundaries(self):
        for role in ('worker', 'checks', 'publisher'):
            with self.subTest(role=role), patch.object(executor.subprocess, 'run',
                    return_value=SimpleNamespace(returncode=0, stdout='')) as run:
                executor.service(role, ['/bin/true'])
            command = run.call_args_list[0].args[0]
            properties = dict(command[i + 1].split('=', 1) for i, value in enumerate(command)
                              if value == '--property')
            families = 'AF_UNIX AF_INET AF_INET6' + (' AF_NETLINK' if role == 'worker' else '')
            self.assertEqual(properties['RestrictAddressFamilies'], families)
            self.assertEqual(properties['CapabilityBoundingSet'], '')
            self.assertEqual(properties['NoNewPrivileges'], 'yes')
            self.assertEqual(properties['IPAddressDeny'], executor.PRIVATE_NETS)
            self.assertEqual(properties['IPAddressAllow'], '127.0.0.53/32')
            self.assertEqual(properties['ProtectSystem'], 'strict')
            self.assertIn('-/run/docker.sock', properties['InaccessiblePaths'])
            self.assertIn('-/var/lib/srv6-mup-background', properties['InaccessiblePaths'])
            self.assertEqual(properties['User'], executor.ACCOUNTS[role])

    def test_strict_events_reject_errors_even_with_turn_completed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'worker.jsonl'
            valid = '\n'.join(map(json.dumps, self.EVENTS)) + '\n'
            path.write_text(valid)
            executor.validate_worker_events(path)
            bad = ['', '{}\n', 'diagnostic, not JSON\n', '[]\n', '{"type":"item.completed","item":null}\n',
                   '\n'.join(map(json.dumps, self.EVENTS[:-1])),
                   valid + json.dumps({'type': 'error', 'message': 'failure'}),
                   valid + json.dumps({'type': 'turn.failed'}),
                   json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': 'missing host'}}) + '\n' + valid]
            for content in bad:
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(executor.WorkerExecutionError):
                        executor.validate_worker_events(path)

    def test_service_separates_diagnostics_and_rejects_zero_exit_tool_error(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / 'worker.jsonl'
            events = list(self.EVENTS)
            def command(argv, **kwargs):
                if argv[0] == 'systemd-run':
                    kwargs['stdout'].write('\n'.join(map(json.dumps, events)) + '\n')
                    kwargs['stderr'].write('ordinary diagnostic, not JSON\n')
                return SimpleNamespace(returncode=0)
            with patch.object(executor.subprocess, 'run', side_effect=command):
                executor.service('worker', ['codex', 'exec', '--json'], output_file=log, json_events=True)
                executor.validate_worker_events(log)
                errors = Path(str(log) + '.stderr.log')
                self.assertIn('ordinary diagnostic', errors.read_text())
                self.assertNotIn('ordinary diagnostic', log.read_text())
                self.assertEqual(errors.stat().st_mode & 0o777, 0o600)
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)
                events.insert(0, {'type': 'item.completed', 'item': {'type': 'error', 'message': 'missing host'}})
                with self.assertRaisesRegex(executor.WorkerExecutionError, 'execution error'):
                    executor.service('worker', ['codex', 'exec', '--json'], output_file=Path(root) / 'bad.jsonl', json_events=True)

    def test_stderr_evidence_is_never_overwritten_and_invalid_usage_does_not_run(self):
        with tempfile.TemporaryDirectory() as root, patch.object(executor.subprocess, 'run') as run:
            log = Path(root) / 'worker.jsonl'
            errors = Path(str(log) + '.stderr.log')
            errors.write_text('retained')
            with self.assertRaises(FileExistsError):
                executor.service('worker', ['codex', '--json'], output_file=log, json_events=True)
            self.assertEqual(errors.read_text(), 'retained')
            self.assertFalse(any(call.args[0][0] == 'systemd-run' for call in run.call_args_list))
            run.reset_mock()
            with self.assertRaisesRegex(ValueError, 'private output file'):
                executor.service('worker', ['codex', '--json'], json_events=True)
            run.assert_not_called()


class RetryTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = bg.Store(self.temp.name)
        self.policy = bg.load_policy()
        self.task = self.policy['tasks'][0]['id']
        self.run_id = 'a' * 32
        self.state = self.store.read()
        self.state['tasks'][self.task] = {'status': 'needs-decision', 'base': 'b' * 40}
        self.trial = {'mode': 'test-once', 'run_id': self.run_id, 'task': self.task,
                      'outcome': 'needs-decision', 'pr': None, 'status': 'completed'}
        self.path = self.store.directory / 'trials' / self.run_id / 'result.json'
        self.path.parent.mkdir(parents=True)
        bg.atomic_json(self.path, self.trial)
        for mocked in (patch.object(admin, 'STATE', self.store.directory), patch.object(admin, 'protected')):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_explicit_retry_preserves_evidence_base_and_pause(self):
        original = self.path.read_bytes()
        admin.retry_task(self.store, self.state, self.policy, self.task, self.run_id)
        entry = self.store.read()['tasks'][self.task]
        self.assertEqual(entry['status'], 'working')
        self.assertEqual(entry['base'], 'b' * 40)
        self.assertEqual(entry['retry_history'], [{'run_id': self.run_id, 'previous_status': 'needs-decision'}])
        self.assertTrue(self.store.read()['paused'])
        self.assertEqual(self.path.read_bytes(), original)
        entry['status'] = 'needs-decision'
        self.state['tasks'][self.task] = entry
        with self.assertRaisesRegex(ValueError, 'already retried'):
            admin.retry_task(self.store, self.state, self.policy, self.task, self.run_id)

    def test_retry_refuses_active_running_published_wrong_task_and_live_work(self):
        original = copy.deepcopy(self.state)
        for variant in ('running', 'active', 'published', 'receipt', 'wrong-task', 'live', 'complete'):
            state = copy.deepcopy(original)
            policy = copy.deepcopy(self.policy)
            task_id = self.task
            if variant == 'running': state['paused'] = False
            if variant == 'active': state['active'] = {'id': self.run_id}
            if variant == 'published': state['tasks'][self.task]['pr'] = 12
            if variant == 'receipt': state['tasks'][self.task]['receipt'] = {'eligible': True}
            if variant == 'wrong-task': task_id = 'unknown-task'
            if variant == 'live': policy['tasks'][0]['profiles'] = ['compact']
            if variant == 'complete': state['tasks'][self.task]['status'] = 'complete'
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                admin.retry_task(self.store, state, policy, task_id, self.run_id)
        self.trial['task'] = 'other-task'
        bg.atomic_json(self.path, self.trial)
        with self.assertRaisesRegex(ValueError, 'exact unpublished'):
            admin.retry_task(self.store, self.state, self.policy, self.task, self.run_id)
        self.assertFalse(self.store.path.exists())


class ServiceBoundaryTests(unittest.TestCase):
    def test_failure_evidence_is_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root, patch.object(executor.subprocess, 'run') as run:
            log = Path(root) / 'phase.log'
            run.return_value.returncode = 1
            run.return_value.stdout = ''
            with self.assertRaisesRegex(ValueError, 'private evidence retained'):
                executor.service('checks', ['false'], output_file=log)
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            log.write_text('retained evidence')
            with self.assertRaises(FileExistsError):
                executor.service('checks', ['false'], output_file=log)
            self.assertEqual(log.read_text(), 'retained evidence')

    def test_failure_without_log_does_not_claim_evidence_was_saved(self):
        with patch.object(executor.subprocess, 'run') as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ''
            with self.assertRaisesRegex(ValueError, 'output was not persisted'):
                executor.service('checks', ['false'])

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
        previous = os.umask(0o077)
        try:
            executor.develop(self.store, self.state, self.policy, self.operator,
                             self.policy['tasks'][0], 'a' * 32, 'unused')
        finally:
            os.umask(previous)
        self.assertEqual((self.worker / 'work').stat().st_mode & 0o777, 0o755)
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

    def test_reported_tool_error_stops_before_snapshot_checks_review_or_publication(self):
        def blocked(role, argv, **kwargs):
            path = kwargs['output_file']
            events = [{'type': 'item.completed', 'item': {'type': 'error', 'message': 'missing code-mode host'}},
                      *WorkerEventTests.EVENTS]
            path.write_text('\n'.join(map(json.dumps, events)))
            executor.validate_worker_events(path)
        with patch.object(executor, 'service', side_effect=blocked):
            with self.assertRaises(executor.WorkerExecutionError):
                executor.develop(self.store, self.state, self.policy, self.operator,
                                 self.policy['tasks'][0], 'd' * 32, 'unused')
        self.assertFalse((self.install / 'candidates').exists())
        self.assertEqual(self.events, [])
        self.assertTrue((self.state_dir / 'runs' / ('d' * 32) / 'worker.jsonl').exists())

    def test_single_run_uses_real_development_checks_review_and_publication_pipeline(self):
        self.state['paused'] = False
        self.store.save(self.state)
        operator = dict(self.operator, automatic_merge=True)
        def service(role, argv, **kwargs):
            if argv[-2:] in (['login', 'status'], ['auth', 'status']):
                return ''
            return self.service(role, argv, **kwargs)
        with patch.object(executor, 'readiness', return_value=operator), \
                patch.object(executor, 'service', side_effect=service), \
                patch.object(executor, 'resources', return_value={'memory_available_mib': 9000, 'disk_free_gib': 40}), \
                patch.object(executor, 'refresh_reviews') as review, \
                patch.object(executor, 'report') as report:
            executor.run(self.store, self.policy, test_once=True)
        self.assertFalse(review.call_args.args[3]['automatic_merge'])
        self.assertTrue(operator['automatic_merge'])
        self.assertEqual(len([event for event in self.events if event[0] == 'checks']), 5)
        self.assertEqual(len([event for event in self.events if event[0] == 'worker']), 2)
        self.assertEqual(self.store.read()['tasks']['documentation-maintenance']['pr'], 12)
        self.assertEqual(report.call_args.kwargs, {'force': True})
        path, = (self.state_dir / 'trials').glob('*/result.json')
        self.assertEqual(json.loads(path.read_text())['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
