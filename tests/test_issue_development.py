"""Offline contract tests: no credentials, model calls, publication or lab access."""
import copy
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import issue_development as dev
import issue_worker as worker


def config(root):
    value = json.loads((ROOT / 'config/issue-development.example.json').read_text())
    value['state_dir'] = str(root / 'state')
    value['enabled'] = True
    return value


def issue(number=21):
    return {'number': number, 'title': 'Add one regression test', 'state': 'open',
            'user': {'login': 'Ytaihei'}, 'labels': [{'name': 'agent:ready'}],
            'body': '```agent-task\n' + json.dumps({'goal': 'Add a test',
                     'paths': ['tests/'], 'acceptance': 'Preserve existing assertions.'}) + '\n```'}


def events():
    return [{'id': 123, 'event': 'labeled', 'actor': {'login': 'Ytaihei'},
             'label': {'name': 'agent:ready'}}]


class FakeGitHub:
    def __init__(self, cfg):
        self.cfg, self.issue, self.events = cfg, issue(), events()
        self.writes = []
        self.main = 'a' * 40
        self.pr = {'number': 25, 'state': 'open', 'draft': False, 'merged': False,
                   'mergeable': True, 'mergeable_state': 'clean',
                   'head': {'sha': 'b' * 40, 'ref': 'agent/issue-21-trial',
                            'repo': {'full_name': dev.REPOSITORY}},
                   'base': {'sha': self.main, 'ref': 'main'}}
        self.runs = [{'id': n, 'head_sha': 'b' * 40, 'name': name,
                      'app': {'id': 15368}, 'status': 'completed', 'conclusion': 'success'}
                     for n, name in enumerate(['test', 'gitleaks', 'source-and-binaries'], 1)]
        self.protection = {'strict': True, 'contexts': ['test', 'gitleaks', 'source-and-binaries']}

    def authorized(self, number):
        return dev.intake(self.issue, self.events, self.cfg)

    def pages(self, route):
        if route.startswith('/issues?'):
            return [self.issue]
        if route.endswith('/comments'):
            return [{'body': value['body']} for path, method, value in self.writes if path.endswith('/comments')]
        if route == '/pulls?state=open' or route.startswith('/pulls?state=all&head='):
            return []
        raise AssertionError(route)

    def call(self, route, method='GET', data=None):
        if method != 'GET':
            self.writes.append((route, method, data))
            return {'merged': True, 'number': 25}
        if route == '/pulls/25':
            return self.pr
        if route == '/git/ref/heads/main':
            return {'object': {'sha': self.main}}
        if route.startswith('/commits/'):
            return {'check_runs': self.runs}
        if route == '/branches/main/protection/required_status_checks':
            return self.protection
        raise AssertionError(route)


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config(self.root)

    def test_config_defaults_are_disabled(self):
        cfg = dev.load_config(ROOT / 'config/issue-development.example.json')
        self.assertFalse(cfg['enabled'])
        self.assertFalse(cfg['publish'])
        self.assertFalse(cfg['automatic_merge'])
        self.assertIsNone(cfg['review_model'])
        self.assertIsNone(cfg['github_auth_user'])

    def test_existing_v2_config_keeps_token_file_mode(self):
        cfg = dict(self.cfg)
        cfg.pop('github_auth_user')
        path = self.root / 'config.json'
        path.write_text(json.dumps(cfg))
        self.assertIsNone(dev.load_config(path)['github_auth_user'])

    def test_gh_auth_requires_exclusive_explicit_account(self):
        for user, token in [('', None), ('bad/name', None), (False, None), ('Ytaihei', '/private/token')]:
            with self.subTest(user=user, token=token):
                path = self.root / 'config.json'
                path.write_text(json.dumps({**self.cfg, 'github_auth_user': user, 'github_token_file': token}))
                with self.assertRaises(ValueError):
                    dev.load_config(path)

    def test_gh_auth_uses_fixed_host_account_and_clean_environment(self):
        cfg = {**self.cfg, 'github_auth_user': 'Ytaihei', 'github_token_file': None}
        result = subprocess.CompletedProcess([], 0, 'private-token\n', '')
        with patch.dict(os.environ, {'GH_TOKEN': 'wrong', 'GITHUB_TOKEN': 'wrong',
                                     'GH_CONFIG_DIR': '/candidate/config'}), \
             patch.object(dev.subprocess, 'run', return_value=result) as run:
            self.assertEqual(dev.github_token(cfg), 'private-token')
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/gh', 'auth', 'token', '--hostname', 'github.com', '--user', 'Ytaihei'])
        self.assertEqual(kwargs['cwd'], '/')
        self.assertEqual(kwargs['env']['GH_CONFIG_DIR'], str(Path.home() / '.config/gh'))
        self.assertNotIn('GH_TOKEN', kwargs['env'])
        self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
        self.assertEqual(kwargs['timeout'], 15)
        env = dev.git_auth_env(cfg)
        self.assertEqual(env['ISSUE_GITHUB_AUTH_USER'], 'Ytaihei')
        self.assertNotIn('ISSUE_GITHUB_TOKEN_FILE', env)
        self.assertNotIn('private-token', str(env))

    def test_gh_auth_failure_never_falls_back_or_discloses_output(self):
        cfg = {**self.cfg, 'github_auth_user': 'Ytaihei', 'github_token_file': None}
        for result in [subprocess.CompletedProcess([], 1, 'secret', 'secret'),
                       subprocess.CompletedProcess([], 0, '', ''),
                       subprocess.CompletedProcess([], 0, 'token\nother', ''),
                       subprocess.CompletedProcess([], 0, 'x' * 8193, '')]:
            with patch.object(dev.subprocess, 'run', return_value=result), \
                 patch.object(dev, 'secret') as secret, \
                 self.assertRaisesRegex(ValueError, '^gh authentication unavailable$'):
                dev.github_token(cfg)
            secret.assert_not_called()
        with patch.object(dev.subprocess, 'run', side_effect=subprocess.TimeoutExpired('secret', 15)), \
             self.assertRaisesRegex(ValueError, '^gh authentication unavailable$'):
            dev.github_token(cfg)

    def test_token_file_mode_does_not_invoke_gh(self):
        with patch.object(dev, 'secret', return_value='token') as secret, \
             patch.object(dev.subprocess, 'run') as run:
            self.assertEqual(dev.github_token(self.cfg), 'token')
        secret.assert_called_once_with(self.cfg['github_token_file'])
        run.assert_not_called()

    def test_config_rejects_unknown_keys_and_broad_state(self):
        for change in [{'surprise': True}, {'state_dir': '/'}, {'state_dir': str(Path.home())},
                       {'state_dir': str(ROOT / 'state')}, {'worker_turns': 1000},
                       {'worker_jobs_per_day': 0}, {'automatic_merge': True}, {'enabled': 'yes'}]:
            with self.subTest(change=change):
                cfg = {**self.cfg, **change}
                file = self.root / 'config.json'
                file.write_text(json.dumps(cfg))
                with self.assertRaises(ValueError):
                    dev.load_config(file)

    def test_trusted_issue_and_grant_required(self):
        accepted = dev.intake(issue(), events(), self.cfg)
        self.assertEqual(accepted['number'], 21)
        for change in [{'user': {'login': 'stranger'}}, {'state': 'closed'},
                       {'labels': []}, {'pull_request': {}}, {'number': True}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                dev.intake({**issue(), **change}, events(), self.cfg)
        bad = events()
        bad[0]['actor']['login'] = 'stranger'
        with self.assertRaises(ValueError):
            dev.intake(issue(), bad, self.cfg)
        with self.assertRaises(ValueError):
            dev.intake(issue(), [], self.cfg)

    def test_latest_grant_and_issue_changes_invalidate_snapshot(self):
        before = dev.intake(issue(), events(), self.cfg)
        altered = issue()
        altered['body'] = altered['body'].replace('Add a test', 'Alter behavior')
        self.assertNotEqual(before['fingerprint'], dev.intake(altered, events(), self.cfg)['fingerprint'])
        ev = events() + [{**events()[0], 'id': 124, 'event': 'unlabeled'}]
        with self.assertRaises(ValueError):
            dev.intake(issue(), ev, self.cfg)

    def test_issue_cannot_request_control_plane_paths(self):
        for path in ['scripts/', '.github/', '../secret', '/etc/passwd', 'AGENTS.md',
                     'config/issue-development.example.json', 'tests/../../x', 4]:
            value = issue()
            value['body'] = '```agent-task\n' + json.dumps({'goal': 'x', 'paths': [path], 'acceptance': 'y'}) + '\n```'
            with self.subTest(path=path), self.assertRaises(ValueError):
                dev.intake(value, events(), self.cfg)

    def test_duplicate_or_nonobject_task_refused(self):
        for body in ['```agent-task\n[]\n```', issue()['body'] * 2, 'no task',
                     '```agent-task\n{"goal":"x"}\n```']:
            with self.subTest(body=body), self.assertRaises(ValueError):
                dev.intake({**issue(), 'body': body}, events(), self.cfg)

    def test_state_private_lock_and_default_paused(self):
        store = dev.State(self.root / 'state')
        with store.locked():
            self.assertTrue(store.load()['paused'])
            with self.assertRaises(BlockingIOError), store.locked():
                pass
            store.save(store.load())
        self.assertEqual(store.root.stat().st_mode & 0o077, 0)

    def test_unsafe_state_and_secret_rejected(self):
        target = self.root / 'state'
        target.mkdir(mode=0o755)
        # The coordinator runs with umask 077; explicitly create the unsafe
        # permission fixture instead of letting that mask make it safe.
        target.chmod(0o755)
        with self.assertRaises(ValueError), dev.State(target).locked():
            pass
        token = self.root / 'token'
        token.write_text('test-placeholder-not-a-credential')
        token.chmod(0o644)
        with self.assertRaises(ValueError):
            dev.secret(str(token))
        token.chmod(0o600)
        self.assertEqual(dev.secret(str(token)), 'test-placeholder-not-a-credential')
        link = self.root / 'link'
        link.symlink_to(token)
        with self.assertRaises(OSError):
            dev.secret(str(link))

    def test_collect_idempotent_and_relabel_new_generation(self):
        gh = FakeGitHub(self.cfg)
        coordinator = dev.Coordinator(self.cfg, gh)
        with coordinator.store.locked():
            state = coordinator.store.load()
            coordinator.collect(state)
            coordinator.collect(state)
            self.assertEqual(len(state['jobs']), 1)
            gh.events[0]['id'] = 125
            coordinator.collect(state)
            self.assertEqual(len(state['jobs']), 2)

    def coordinator(self, status='queued'):
        gh = FakeGitHub(self.cfg)
        coordinator = dev.Coordinator(self.cfg, gh)
        with coordinator.store.locked():
            state = coordinator.store.load()
            state['paused'] = False
            coordinator.collect(state)
            job = state['jobs']['21-123']
            job['status'] = status
            coordinator.save(state)
        return coordinator, gh

    def test_disabled_does_not_contact_github(self):
        self.cfg['enabled'] = False
        coordinator = dev.Coordinator(self.cfg)
        with patch.object(coordinator.gh, 'pages') as api, self.assertRaises(ValueError):
            coordinator.tick()
        api.assert_not_called()

    def test_missing_review_stops_without_publication(self):
        coordinator, gh = self.coordinator('awaiting-review')
        coordinator.tick()
        self.assertEqual(coordinator.store.load()['jobs']['21-123']['status'], 'awaiting-review')
        self.assertFalse(gh.writes)

    def test_interrupted_worker_not_restarted(self):
        coordinator, gh = self.coordinator('working')
        with patch.object(coordinator, 'develop') as work:
            coordinator.tick()
        work.assert_not_called()
        self.assertEqual(coordinator.store.load()['jobs']['21-123']['status'], 'interrupted')
        self.assertTrue(coordinator.store.load()['paused'])

    def test_interruption_pauses_other_jobs_before_intake_or_model_calls(self):
        for status in dev.ACTIVE:
            with self.subTest(status=status):
                coordinator, gh = self.coordinator(status)
                state = coordinator.store.load()
                state['jobs']['queued-fixture'] = {**state['jobs']['21-123'],
                                                  'id': 'queued-fixture', 'status': 'queued'}
                coordinator.store.save(state)
                saved_pauses = []
                original_save = coordinator.store.save
                def record_save(value):
                    saved_pauses.append(value['paused'])
                    original_save(value)
                with patch.object(coordinator, 'collect') as collect, \
                        patch.object(coordinator, 'develop') as work, \
                        patch.object(coordinator, 'run_review') as review, \
                        patch.object(coordinator.store, 'save', side_effect=record_save):
                    coordinator.tick()
                self.assertTrue(saved_pauses)
                self.assertTrue(all(saved_pauses))
                collect.assert_not_called()
                work.assert_not_called()
                review.assert_not_called()
                after = coordinator.store.load()
                self.assertTrue(after['paused'])
                self.assertEqual(after['jobs']['queued-fixture']['status'], 'queued')
                self.assertEqual(after['jobs']['21-123']['status'], 'interrupted')

    def test_interrupted_notification_failure_keeps_queue_paused(self):
        coordinator, _ = self.coordinator('checking')
        with patch.object(coordinator, 'notify', side_effect=OSError('fixture')):
            coordinator.tick()
        state = coordinator.store.load()
        self.assertTrue(state['paused'])
        self.assertTrue(state['jobs']['21-123']['notice_pending'])
        self.assertEqual(state['jobs']['21-123']['status'], 'interrupted')

    def test_changed_issue_cancels_before_work(self):
        coordinator, gh = self.coordinator()
        gh.issue['title'] = 'Changed task'
        with patch.object(coordinator, 'develop') as work:
            coordinator.tick()
        work.assert_not_called()
        self.assertEqual(coordinator.store.load()['jobs']['21-123']['status'], 'cancelled')

    def test_failed_work_pauses_and_retains_run(self):
        coordinator, gh = self.coordinator()
        with patch.object(coordinator, 'develop', side_effect=ValueError('private error content')):
            coordinator.tick()
        state = coordinator.store.load()
        self.assertTrue(state['paused'])
        self.assertEqual(state['jobs']['21-123']['status'], 'failed')
        log = (coordinator.store.root / 'runs/intake/failure.json').read_text()
        self.assertNotIn('private error content', log)

    def test_daily_budget_blocks_model_call(self):
        coordinator, gh = self.coordinator()
        state = coordinator.store.load()
        state['worker_jobs'][dev.now()[:10]] = self.cfg['worker_jobs_per_day']
        coordinator.save(state)
        with patch.object(coordinator, 'develop') as work:
            coordinator.tick()
        work.assert_not_called()

    def merge_job(self):
        return {'number': 21, 'id': '21-123', 'pr': 25, 'run': 'trial', 'status': 'waiting-ci',
                'head': 'b' * 40, 'base': 'a' * 40, 'branch': 'agent/issue-21-trial',
                'classification': {'eligible': True}, 'local_checks_passed': True,
                'review': {'head': 'b' * 40, 'approved': True, 'requires_human': False,
                           'summary': 'Reviewed', 'findings': []},
                'fingerprint': dev.intake(issue(), events(), self.cfg)['fingerprint'],
                'reviewed_at': datetime.now(timezone.utc).isoformat()}

    def test_merge_off_stops_ready_without_write(self):
        coordinator, gh = self.coordinator()
        state = coordinator.store.load()
        job = self.merge_job()
        coordinator.merge(state, job)
        self.assertEqual(job['status'], 'ready-to-merge')
        self.assertFalse(gh.writes)

    def test_all_change_categories_can_merge_after_independent_approval(self):
        for name in ['internal/auth/session.go', 'scripts/issue_development.py',
                     'AGENTS.md', '.github/workflows/ci.yml', 'LICENSE',
                     'config/versions.lock.yml', 'go.mod', 'tests/test_example.py']:
            with self.subTest(path=name):
                coordinator, gh = self.coordinator()
                self.cfg['publish'] = self.cfg['automatic_merge'] = True
                state, job = coordinator.store.load(), self.merge_job()
                job['classification'] = dev.classify_candidate(
                    [{'path': name, 'added': 2, 'deleted': 1}], dev.load_policy())
                coordinator.merge(state, job)
                self.assertEqual(job['status'], 'merged')
                self.assertEqual(gh.writes[-1][0], '/pulls/25/merge')

    def test_category_policy_preserves_candidate_bounds(self):
        policy = dev.load_policy()
        for entries in [[], [{'path': 'internal/auth.go', 'added': 301, 'deleted': 0}],
                        [{'path': f'internal/file{i}.go', 'added': 1, 'deleted': 0}
                         for i in range(11)]]:
            with self.subTest(entries=entries):
                self.assertFalse(dev.classify_candidate(entries, policy)['eligible'])
        with self.assertRaises(ValueError):
            dev.classify_candidate([{'path': '../private', 'added': 1, 'deleted': 0}], policy)

    def test_all_category_policy_does_not_override_review_rejection(self):
        for review_change in [{'approved': False}, {'requires_human': True},
                              {'findings': ['Missing security evidence']}]:
            with self.subTest(review=review_change):
                coordinator, gh = self.coordinator()
                self.cfg['publish'] = self.cfg['automatic_merge'] = True
                state, job = coordinator.store.load(), self.merge_job()
                job['classification'] = dev.classify_candidate(
                    [{'path': 'internal/auth.go', 'added': 1, 'deleted': 1}], dev.load_policy())
                job['review'].update(review_change)
                coordinator.merge(state, job)
                self.assertFalse(gh.writes)

    def test_merge_requires_matching_head_base_checks_and_scope(self):
        for mutation in ['head', 'base', 'failed-check', 'wrong-app', 'ineligible', 'stale-review']:
            with self.subTest(mutation=mutation):
                coordinator, gh = self.coordinator()
                self.cfg['publish'] = self.cfg['automatic_merge'] = True
                state, job = coordinator.store.load(), self.merge_job()
                if mutation == 'head':
                    gh.pr['head']['sha'] = 'c' * 40
                elif mutation == 'base':
                    gh.main = 'c' * 40
                elif mutation == 'failed-check':
                    gh.runs.append({**gh.runs[0], 'id': 100, 'conclusion': 'failure'})
                elif mutation == 'wrong-app':
                    gh.runs[0]['app']['id'] = 999
                elif mutation == 'ineligible':
                    job['classification']['eligible'] = False
                else:
                    job['reviewed_at'] = '2000-01-01T00:00:00+00:00'
                coordinator.merge(state, job)
                self.assertFalse(gh.writes)

    def test_merge_strict_protection_and_expected_sha(self):
        coordinator, gh = self.coordinator()
        self.cfg['publish'] = self.cfg['automatic_merge'] = True
        state, job = coordinator.store.load(), self.merge_job()
        gh.protection['strict'] = False
        with self.assertRaises(ValueError):
            coordinator.merge(state, job)
        self.assertFalse(gh.writes)
        gh.protection['strict'] = True
        coordinator.merge(state, job)
        self.assertEqual(job['status'], 'merged')
        self.assertEqual(gh.writes[-1], ('/pulls/25/merge', 'PUT', {'sha': 'b'*40, 'merge_method': 'squash'}))

    def test_new_failed_ci_clears_previous_ready_status(self):
        coordinator, gh = self.coordinator()
        state, job = coordinator.store.load(), self.merge_job()
        job['status'] = 'ready-to-merge'
        gh.runs.append({**gh.runs[0], 'id': 100, 'conclusion': 'failure'})
        coordinator.merge(state, job)
        self.assertEqual(job['status'], 'waiting-ci')
        self.assertFalse(gh.writes)

    def test_review_invalid_schema_cannot_approve(self):
        good = self.merge_job()['review']
        self.assertTrue(dev.validate_review(good, 'b'*40))
        for mutation in [{'approved': 'true'}, {'head': 'c'*40}, {'findings': 'none'}, {'extra': 1}]:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                dev.validate_review({**good, **mutation}, 'b'*40)
        self.assertFalse(dev.validate_review({**good, 'requires_human': True}, 'b'*40))
        self.assertFalse(dev.validate_review({**good, 'findings': ['bug']}, 'b'*40))

    def test_publish_checks_review_then_uses_expected_commit(self):
        coordinator, gh = self.coordinator()
        self.cfg['publish'] = True
        state, job = coordinator.store.load(), self.merge_job()
        with patch.object(dev, 'git', side_effect=['b'*40, '', '']) as git:
            coordinator.publish(state, job)
        self.assertEqual(job['status'], 'waiting-ci')
        self.assertEqual(job['pr'], 25)
        self.assertIn('b'*40 + ':refs/heads/agent/issue-21-trial', git.call_args.args)
        self.assertEqual(gh.writes[-1][0:2], ('/pulls', 'POST'))
        job['local_checks_passed'] = False
        with patch.object(dev, 'git') as git, self.assertRaises(ValueError):
            coordinator.publish(state, job)
        git.assert_not_called()

    def test_publish_recovery_reuses_open_and_closed_pr_without_push(self):
        for status in ('open', 'closed'):
            with self.subTest(status=status):
                coordinator, gh = self.coordinator()
                self.cfg['publish'] = True
                state, job = coordinator.store.load(), self.merge_job()
                gh.pr['state'] = status
                with patch.object(gh, 'pages', return_value=[gh.pr]), \
                     patch.object(dev, 'git', side_effect=['b'*40, '']) as git:
                    coordinator.publish(state, job)
                self.assertEqual(git.call_count, 2)
                self.assertFalse(gh.writes)
                self.assertEqual(job['pr'], 25)
                self.assertEqual(job['status'], 'waiting-ci' if status == 'open' else 'needs-decision')

    def test_publish_edit_during_push_prevents_pr_creation(self):
        coordinator, gh = self.coordinator()
        self.cfg['publish'] = True
        state, job = coordinator.store.load(), self.merge_job()
        def git(repo, *args, **kwargs):
            if 'rev-parse' in args:
                return 'b'*40
            if 'push' in args:
                gh.issue['title'] = 'Task changed during push'
            return ''
        with patch.object(dev, 'git', side_effect=git):
            coordinator.publish(state, job)
        self.assertEqual(job['status'], 'cancelled')
        self.assertFalse(gh.writes)

    def test_notification_is_fixed_and_idempotent(self):
        coordinator, gh = self.coordinator()
        self.cfg['publish'] = True
        state = coordinator.store.load()
        job = state['jobs']['21-123']
        job['summary'] = 'private raw text never publish'
        coordinator.notify(state, job)
        job['notice_pending'] = True
        coordinator.notify(state, job)
        self.assertEqual(len(gh.writes), 1)
        self.assertNotIn('private raw text', gh.writes[0][2]['body'])

    def test_review_budget_reserved_even_if_review_fails(self):
        coordinator, gh = self.coordinator('awaiting-review')
        self.cfg['review_model'], self.cfg['review_session_dir'] = 'example-model', '/nonexistent'
        self.cfg['review_included_only_confirmed'] = True
        with patch.object(dev, 'review', side_effect=ValueError('failure')):
            coordinator.tick()
        self.assertEqual(coordinator.store.load()['review_calls'][dev.now()[:10]], 1)

    def test_subscription_limit_holds_review_without_publication_or_repeated_calls(self):
        coordinator, gh = self.coordinator('awaiting-review')
        state = coordinator.store.load()
        state['jobs']['21-123']['run'] = 'fixture'
        (coordinator.store.root / 'runs' / 'fixture').mkdir(parents=True)
        coordinator.store.save(state)
        self.cfg.update(review_model='example-model', review_session_dir='/nonexistent',
                        review_included_only_confirmed=True, review_calls_per_day=10)
        with patch.object(dev, 'review', side_effect=dev.ReviewBlocked('usage-limit')) as model:
            coordinator.tick()
            coordinator.tick()
        state = coordinator.store.load()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(state['jobs']['21-123']['status'], 'awaiting-review')
        self.assertEqual(state['review_hold']['reason'], 'usage-limit')
        self.assertEqual(state['review_calls'][dev.now()[:10]], 1)
        self.assertFalse(gh.writes)

    def test_old_paid_configuration_is_rejected(self):
        cfg = {**self.cfg, 'version': 1, 'review_api_key_file': '/private/key'}
        path = self.root / 'old-config.json'
        path.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError):
            dev.load_config(path)

    def test_review_resume_does_not_reset_budget_or_unpause_development(self):
        coordinator, _ = self.coordinator('awaiting-review')
        state = coordinator.store.load()
        state.update(paused=True, review_hold={'reason': 'usage-limit'}, review_calls={dev.now()[:10]: 1})
        coordinator.store.save(state)
        path = self.root / 'operator.json'
        path.write_text(json.dumps(self.cfg))
        original_umask = os.umask(0o077)
        try:
            dev.main(['--config', str(path), 'review-resume'])
        finally:
            os.umask(original_umask)
        after = coordinator.store.load()
        self.assertNotIn('review_hold', after)
        self.assertTrue(after['paused'])
        self.assertEqual(after['review_calls'], state['review_calls'])


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        (self.work / 'tests').mkdir(parents=True)
        (self.work / 'tests/test_sample.py').write_text('old value\n')
        (self.work / 'config').mkdir()
        (self.work / 'config/public-source.json').write_text(json.dumps({'version': 1, 'files': [
            'tests/test_sample.py', 'config/public-source.json', 'config/documentation.json']}))
        (self.work / 'config/documentation.json').write_text(json.dumps({'version': 1, 'translations': []}))
        self.cfg = config(self.root)

    def baseline(self):
        return {name: hashlib.sha256((self.work / name).read_bytes()).hexdigest()
                for name in worker.tree_files(self.work)}

    def test_unit_copy_refreshes_only_changed_manifests_and_preserves_work(self):
        (self.work / 'docs').mkdir()
        for name in ['guide.md', 'guide.ja.md']:
            (self.work / 'docs' / name).write_text('original\n')
        policy = self.work / 'config/public-source.json'
        inventory = json.loads(policy.read_text())
        inventory['files'] += ['docs/guide.md', 'docs/guide.ja.md']
        policy.write_text(json.dumps(inventory))
        manifest = self.work / 'config/documentation.json'
        manifest.write_text(json.dumps({'version': 1, 'translations': [{
            'source': 'docs/guide.md', 'translation': 'docs/guide.ja.md',
            'source_sha256': hashlib.sha256(b'original\n').hexdigest(),
            'translation_sha256': 'preexisting-invalid-hash-must-not-be-hidden'}]}))
        original_policy, original_manifest = policy.read_bytes(), manifest.read_bytes()
        baseline = self.baseline()
        (self.work / 'docs/guide.md').write_text('updated\n')
        (self.work / 'tests/test_added.py').write_text('# new regression\n')
        target = self.root / 'prepared'
        worker.prepare_unit_source(self.work, target, ['docs/', 'tests/'], baseline)
        self.assertEqual(policy.read_bytes(), original_policy)
        self.assertEqual(manifest.read_bytes(), original_manifest)
        row = json.loads((target / 'config/documentation.json').read_text())['translations'][0]
        self.assertEqual(row['source_sha256'], hashlib.sha256(b'updated\n').hexdigest())
        self.assertEqual(row['translation_sha256'], 'preexisting-invalid-hash-must-not-be-hidden')
        self.assertIn('tests/test_added.py', json.loads((target / 'config/public-source.json').read_text())['files'])
        (target / 'docs/guide.md').write_text('copy-only\n')
        self.assertEqual((self.work / 'docs/guide.md').read_text(), 'updated\n')

    def test_unit_copy_refuses_scope_changes_and_deletions(self):
        baseline = self.baseline()
        manifest = self.work / 'config/documentation.json'
        original = manifest.read_text()
        manifest.write_text(original + '\n')
        with self.assertRaisesRegex(ValueError, 'scope'):
            worker.prepare_unit_source(self.work, self.root / 'scope', ['tests/'], baseline)
        manifest.write_text(original)
        (self.work / 'tests/test_sample.py').unlink()
        with self.assertRaisesRegex(ValueError, 'deleted'):
            worker.prepare_unit_source(self.work, self.root / 'deleted', ['tests/'], baseline)

    def test_unit_copy_refuses_unreviewed_new_paths_and_unsafe_manifest_paths(self):
        baseline = self.baseline()
        (self.work / 'docs').mkdir()
        added = self.work / 'docs/new.md'
        added.write_text('new documentation\n')
        with self.assertRaisesRegex(ValueError, 'inventory review'):
            worker.prepare_unit_source(self.work, self.root / 'new-path', ['docs/'], baseline)
        added.unlink()
        manifest = self.work / 'config/documentation.json'
        manifest.write_text(json.dumps({'translations': [{'source': '../outside', 'translation': 'docs/a.md'}]}))
        with self.assertRaisesRegex(ValueError, 'unsafe relative'):
            worker.refresh_manifests(self.work, [])

    def test_test_log_tail_is_bounded_but_full_failure_log_is_retained(self):
        responses = iter([
            {'done': True, 'message': {'role': 'assistant', 'tool_calls': [
                {'function': {'name': 'run_tests', 'arguments': {}}}]}},
            {'done': True, 'message': {'role': 'assistant', 'content': 'Tests failed; stopping.'}}])
        full_log = 'large test detail\n' * 2000 + 'FAIL: real_failure\n'
        def fake_check(work, cfg, argv, log, seconds):
            self.assertNotEqual(work, self.work)
            self.assertEqual((work / 'tests/test_sample.py').read_text(), 'old value\n')
            self.assertEqual(argv, ['make', 'unit', 'GO=/toolchain/go/bin/go'])
            log.write_text(full_log)
            return 7
        with patch.object(worker, 'isolated', side_effect=fake_check):
            worker.worker(self.work, self.cfg, {'paths': ['tests/']}, self.root / 'evidence',
                          transport=lambda *a, **kw: next(responses))
        evidence = self.root / 'evidence'
        self.assertEqual((evidence / 'unit-0.log').read_text(), full_log)
        records = [json.loads(line) for line in (evidence / 'worker.jsonl').read_text().splitlines()]
        result = next(row['result'] for row in records if row.get('tool') == 'run_tests')
        self.assertTrue(result.startswith('exit=7\n'))
        self.assertIn('FAIL: real_failure', result)
        self.assertLess(len(result), 2100)

    def test_read_pages_preserve_lines_and_report_continuation(self):
        text = ''.join(f'line {n}\n' for n in range(95)) + 'last without newline'
        (self.work / 'tests/test_sample.py').write_text(text)
        first = json.loads(worker.read_page(self.work, 'tests/test_sample.py'))
        self.assertEqual((first['start_line'], first['end_line'], first['total_lines']), (1, 40, 96))
        self.assertEqual(first['next_start_line'], 41)
        second = json.loads(worker.read_page(self.work, 'tests/test_sample.py', 41, 40))
        last = json.loads(worker.read_page(self.work, 'tests/test_sample.py', 81, 120))
        self.assertEqual(second['next_start_line'], 81)
        self.assertIsNone(last['next_start_line'])
        self.assertEqual(first['content'] + second['content'] + last['content'], text)

    def test_read_page_bounds_utf8_bytes_not_characters(self):
        line = '日' * 100 + '\n'
        (self.work / 'tests/test_sample.py').write_text(line * 40)
        page = json.loads(worker.read_page(self.work, 'tests/test_sample.py', max_lines=120))
        self.assertLessEqual(len(page['content'].encode('utf-8')), worker.READ_BYTES)
        self.assertEqual(page['content'], line * 13)
        self.assertEqual(page['next_start_line'], 14)

    def test_read_page_empty_file_and_oversized_line(self):
        path = self.work / 'tests/test_sample.py'
        path.write_text('')
        page = json.loads(worker.read_page(self.work, 'tests/test_sample.py'))
        self.assertEqual(page, {'start_line': 1, 'end_line': 0, 'total_lines': 0,
                                'next_start_line': None, 'content': ''})
        path.write_text('x' * worker.READ_BYTES)
        self.assertEqual(len(json.loads(worker.read_page(self.work, 'tests/test_sample.py'))['content']),
                         worker.READ_BYTES)
        path.write_text('short\n' + 'x' * (worker.READ_BYTES + 1))
        self.assertEqual(json.loads(worker.read_page(self.work, 'tests/test_sample.py'))['next_start_line'], 2)
        with self.assertRaisesRegex(ValueError, 'single line'):
            worker.read_page(self.work, 'tests/test_sample.py', 2)

    def test_read_page_rejects_invalid_ranges_and_paths(self):
        for start, count in [(0, 40), (-1, 40), (True, 40), (1.0, 40), ('1', 40),
                             (2, 40), (1, 0), (1, 121), (1, True), (1, 1.5), (1, '40')]:
            with self.subTest(start=start, count=count), self.assertRaises(ValueError):
                worker.read_page(self.work, 'tests/test_sample.py', start, count)
        (self.work / 'tests/link.py').symlink_to(self.root / 'private')
        os.link(self.work / 'tests/test_sample.py', self.work / 'tests/hard.py')
        for name in ['../secret', '/etc/passwd', '.git/config', 'tests/link.py', 'tests/hard.py']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                worker.read_page(self.work, name)

    def test_long_paired_documents_do_not_exhaust_prompt_on_first_reads(self):
        names = ['guide.md', 'guide.ja.md', 'glossary.md', 'glossary.ja.md']
        for name in names:
            (self.work / 'tests' / name).write_text(('日英の長い文書' * 20 + '\n') * 200)
        calls = [{'function': {'name': 'read_file', 'arguments': {'path': 'tests/' + name}}}
                 for name in names]
        requests = []
        def transport(url, data, **kwargs):
            requests.append(len(json.dumps(data['messages'])))
            if len(requests) == 1:
                response_calls = calls
            elif len(requests) == 2:
                self.assertLess(requests[-1], 60000)
                response_calls = [{'function': {'name': 'run_tests', 'arguments': {}}}]
            else:
                return {'done': True, 'message': {'role': 'assistant', 'content': 'Tests observed.'}}
            return {'done': True, 'message': {'role': 'assistant', 'tool_calls': response_calls}}
        def fake_check(work, cfg, argv, log, seconds):
            log.write_text('OK\n')
            return 0
        with patch.object(worker, 'isolated', side_effect=fake_check):
            result = worker.worker(self.work, self.cfg, {'paths': ['tests/']}, self.root / 'evidence',
                                   transport=transport)
        self.assertEqual(result, 'Tests observed.')
        self.assertEqual(len(requests), 3)

    def test_patch_scope_and_unique_match(self):
        worker.edit(self.work, ['tests/'], 'tests/test_sample.py', 'old', 'new')
        self.assertEqual((self.work / 'tests/test_sample.py').read_text(), 'new value\n')
        for name in ['../secret', '/etc/passwd', '.git/config', 'scripts/example.py', 'AGENTS.md']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                worker.edit(self.work, ['tests/'], name, '', 'x')
        with self.assertRaises(ValueError):
            worker.edit(self.work, ['tests/'], 'tests/test_sample.py', '', 'x')
        with self.assertRaises(ValueError):
            worker.edit(self.work, ['tests/'], 'tests/test_sample.py', 'missing', 'x')

    def test_symlink_and_hardlink_refused(self):
        (self.work / 'tests/link.py').symlink_to(self.root / 'private')
        with self.assertRaises(ValueError):
            worker.edit(self.work, ['tests/'], 'tests/link.py', '', 'x')
        os.link(self.work / 'tests/test_sample.py', self.work / 'tests/hard.py')
        with self.assertRaises(ValueError):
            worker.regular(self.work, 'tests/hard.py')

    def test_sandbox_has_no_host_network_home_or_credentials(self):
        cfg = {**self.cfg, 'go_root': None, 'go_module_cache': None, 'verification_cache': None}
        args = worker.sandbox_command(self.work, cfg, ['python3', '-m', 'unittest'])
        self.assertIn('--unshare-all', args)
        self.assertIn('--clearenv', args)
        self.assertIn('--cap-drop', args)
        self.assertNotIn('--share-net', args)
        self.assertNotIn(str(Path.home()), args)
        self.assertNotIn('sudo', args)
        self.assertIn('--ro-bind', args)
        self.assertNotIn('--bind', args)

    def test_worker_tool_roundtrip_and_observed_tests(self):
        def response(calls=None, content=''):
            return {'done': True, 'message': {'role': 'assistant', 'content': content,
                    **({'tool_calls': calls} if calls else {})}}
        responses = iter([
            response([{'function': {'name': 'read_file', 'arguments': {'path': 'tests/test_sample.py'}}}]),
            response([{'function': {'name': 'read_file', 'arguments': {'path': 'tests/test_sample.py',
                                                                     'start_line': 1, 'max_lines': 1}}}]),
            response([{'function': {'name': 'apply_patch', 'arguments': {'path': 'tests/test_sample.py',
                                                                          'old': 'old', 'new': 'new'}}}]),
            response([{'function': {'name': 'run_tests', 'arguments': {}}}]),
            response(content='Done; tests ran.')])
        def fake_check(work, cfg, argv, log, seconds):
            log.write_text('OK\n')
            return 0
        with patch.object(worker, 'isolated', side_effect=fake_check):
            result = worker.worker(self.work, self.cfg, {'paths': ['tests/']}, self.root / 'evidence',
                                   transport=lambda *a, **kw: next(responses))
        self.assertIn('Done', result)
        self.assertEqual((self.work / 'tests/test_sample.py').read_text(), 'new value\n')
        log = [json.loads(line) for line in (self.root / 'evidence/worker.jsonl').read_text().splitlines()]
        reads = [json.loads(item['result']) for item in log if item.get('tool') == 'read_file']
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(page['content'] == 'old value\n' for page in reads))

    def test_execution_limits_accommodate_existing_boundary_tests(self):
        with patch.object(worker.resource, 'setrlimit') as limit:
            worker.limits()
        calls = {call.args[0]: call.args[1] for call in limit.call_args_list}
        self.assertEqual(calls[worker.resource.RLIMIT_FSIZE], (256 * 1024 * 1024,) * 2)
        self.assertEqual(calls[worker.resource.RLIMIT_CORE], (0, 0))
        self.assertLess(worker.MAX_FILE, 65 * 1024 * 1024)

    def test_worker_cannot_self_assert_tests(self):
        response = {'done': True, 'message': {'role': 'assistant', 'content': 'All tests passed!'}}
        with self.assertRaises(ValueError):
            worker.worker(self.work, self.cfg, {'paths': ['tests/']}, self.root / 'evidence',
                          transport=lambda *a, **kw: response)

    def test_redirects_refused(self):
        for handler in [worker.NoRedirect(), dev.NoRedirect()]:
            with self.assertRaises(ValueError):
                handler.redirect_request(None, None, None, None, None, None)

    def test_empty_completion_cannot_bypass_worker_checks(self):
        response = {'done': True, 'message': {'role': 'assistant', 'content': ''}}
        def transport(url, data, **kwargs):
            self.assertFalse(data['think'])
            self.assertEqual(data['keep_alive'], 300)
            self.assertLessEqual(kwargs['seconds'], self.cfg['worker_seconds'])
            self.assertEqual({t['function']['name'] for t in data['tools']},
                             {'read_file', 'apply_patch', 'run_tests'})
            return response
        with self.assertRaises(ValueError):
            worker.worker(self.work, self.cfg, {'paths': ['tests/']}, self.root / 'evidence',
                          transport=transport)

    def test_worker_records_only_nonnegative_integer_durations(self):
        response = {'done': True, 'message': {'role': 'assistant', 'content': 'No tests.'},
                    'total_duration': 300, 'load_duration': 0,
                    'prompt_eval_duration': -1, 'eval_duration': 'unexpected text'}
        with self.assertRaises(ValueError):
            worker.worker(self.work, self.cfg, {'paths': ['tests/']}, self.root / 'evidence',
                          transport=lambda *a, **kw: response)
        metadata = json.loads((self.root / 'evidence/worker.jsonl').read_text().splitlines()[0])
        self.assertEqual(metadata['timings_ns'], {'total_duration': 300, 'load_duration': 0})


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.cfg = config(self.root)
        files = {'tests/test_sample.py': 'import unittest\n',
                 'internal/auth/session.go': 'package auth\nconst Timeout = 10\n',
                 'scripts/example.sh': '#!/bin/sh\nexit 0\n',
                 '.github/workflows/ci.yml': 'name: fixture\n',
                 'config/documentation.json': '{"version": 1, "translations": []}\n'}
        files['config/public-source.json'] = json.dumps({'version': 1,
            'files': sorted([*files, 'config/public-source.json']), 'private_only': []}, indent=2) + '\n'
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            path.chmod(0o700 if name == 'scripts/example.sh' else 0o600)
        dev.git(self.repo, 'init', '-q', '-b', 'main')
        dev.git(self.repo, 'add', '.')
        identity = {'GIT_AUTHOR_NAME': 'Fixture', 'GIT_COMMITTER_NAME': 'Fixture',
                    'GIT_AUTHOR_EMAIL': 'fixture@example.invalid', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        dev.git(self.repo, 'commit', '-qm', 'Fixture', env=identity)
        self.base = dev.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.work = self.root / 'work'
        dev.export_snapshot(self.repo, self.base, self.work)

    def test_snapshot_includes_public_ci_but_not_git(self):
        self.assertTrue((self.work / '.github/workflows/ci.yml').is_file())
        self.assertFalse((self.work / '.git').exists())
        self.assertFalse(worker.writable('.github/workflows/ci.yml', ['.github/']))

    def test_freeze_ignores_private_umask_but_preserves_executable_bits(self):
        with (self.work / 'tests/test_sample.py').open('a') as out:
            out.write('\n# additive fixture\n')
        head, classification = dev.freeze(self.repo, self.work, self.base, ['tests/'], self.cfg)
        self.assertNotEqual(head, self.base)
        self.assertTrue(classification['eligible'])

    def test_unapproved_edit_and_executable_change_refused(self):
        path = self.work / '.github/workflows/ci.yml'
        path.chmod(0o755)
        with self.assertRaises(ValueError):
            dev.freeze(self.repo, self.work, self.base, ['tests/'], self.cfg)
        path.chmod(0o644)
        path.write_text('name: changed\n')
        with self.assertRaises(ValueError):
            dev.freeze(self.repo, self.work, self.base, ['tests/'], self.cfg)

    def test_runtime_edit_is_eligible_without_legacy_category_gate(self):
        (self.work / 'internal/auth/session.go').write_text('package auth\nconst Timeout = 20\n')
        head, classification = dev.freeze(self.repo, self.work, self.base, ['internal/'], self.cfg)
        self.assertNotEqual(head, self.base)
        self.assertTrue(classification['eligible'])

    def test_only_test_inventory_additions_allowed(self):
        (self.work / 'tests/test_added.py').write_text('import unittest\n')
        head, classification = dev.freeze(self.repo, self.work, self.base, ['tests/'], self.cfg)
        self.assertTrue(classification['eligible'])
        inventory = json.loads(dev.git(self.repo, 'show', head + ':config/public-source.json'))
        self.assertIn('tests/test_added.py', inventory['files'])

    def test_review_transport_no_tools_and_exact_head_schema(self):
        self.cfg.update(review_model='configured-review-model', review_session_dir=str(self.root / 'session'),
                        review_included_only_confirmed=True)
        result = {'head': self.base, 'approved': True, 'requires_human': False,
                  'summary': 'Fixture only', 'findings': []}
        calls = []
        def transport(body, config):
            calls.append((body, config))
            return json.dumps(result)
        job = {'base': self.base, 'head': self.base, 'task': {'goal': 'fixture'}}
        self.assertEqual(dev.review(job, self.repo, self.cfg, transport), result)
        body = calls[0][0]
        self.assertNotIn('tools', body)
        self.assertNotIn('max_output_tokens', body)
        self.assertFalse(body['store'])
        self.assertTrue(body['stream'])
        self.assertIsInstance(body['input'], list)
        self.assertEqual(body['text']['format']['schema'], dev.REVIEW_SCHEMA)
        self.assertIn('Category alone is not a reason to require human review', body['instructions'])

    def test_review_sends_head_bound_check_observations_not_private_logs(self):
        directory = self.root / 'checks'
        directory.mkdir()
        for index in range(len(worker.CHECK_COMMANDS)):
            (directory / f'check-{index}.log').write_text('private fixture log text')
        evidence = dev.passed_check_evidence(self.base, directory)
        job = {'base': self.base, 'head': self.base, 'task': {'goal': 'fixture'},
               'check_evidence': evidence, 'context_paths': ['scripts/example.sh']}
        result = {'head': self.base, 'approved': False, 'requires_human': True,
                  'summary': 'fixture', 'findings': []}
        sent = []
        def transport(body, config):
            sent.append(json.loads(body['input'][0]['content']))
            return json.dumps(result)
        dev.review(job, self.repo, self.cfg, transport)
        self.assertEqual(sent[0]['check_evidence']['head'], self.base)
        self.assertEqual([x['argv'] for x in evidence['commands']], worker.CHECK_COMMANDS)
        self.assertTrue(all(x['exit_code'] == 0 for x in evidence['commands']))
        self.assertNotIn('private fixture log text', json.dumps(sent))
        self.assertIn('scripts/example.sh', sent[0]['additional_context'])
        evidence['head'] = 'f' * 40
        with self.assertRaises(ValueError):
            dev.review(job, self.repo, self.cfg, transport)
        self.assertEqual(len(sent), 1)

    def test_review_rejects_nonpublic_context_before_transport(self):
        with patch.object(dev, 'review_text') as transport, self.assertRaises(ValueError):
            dev.review({'base': self.base, 'head': self.base, 'task': {},
                        'context_paths': ['private.txt']}, self.repo, self.cfg, transport)
        transport.assert_not_called()

    def test_local_review_disabled_checks_and_budget_gates(self):
        self.cfg.update(enabled=False, review_model='fixture-model',
                        review_session_dir=str(self.root / 'session'), review_included_only_confirmed=True)
        task = self.root / 'task.json'
        task.write_text(json.dumps({'goal': 'Fixture', 'paths': ['tests/'], 'acceptance': 'Preserve tests'}))
        cfg = self.root / 'operator.json'
        cfg.write_text(json.dumps(self.cfg))
        argv = ['--config', str(cfg), 'review-local', '--source', str(self.repo),
                '--base', self.base, '--head', self.base, '--task', str(task)]
        original_umask = os.umask(0o077)
        self.addCleanup(os.umask, original_umask)
        with patch.object(dev, 'checks', return_value=False), \
                patch.object(dev, 'review') as model, patch.object(dev.GitHub, 'call') as remote, \
                redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            dev.main(argv)
        model.assert_not_called()
        remote.assert_not_called()
        store = dev.State(self.cfg['state_dir'])
        self.assertEqual(store.load()['review_calls'], {})
        def passing(work, config, evidence):
            evidence.mkdir()
            for index in range(len(worker.CHECK_COMMANDS)):
                (evidence / f'check-{index}.log').write_text('fixture')
            return True
        approved = {'head': self.base, 'approved': True, 'requires_human': False,
                    'summary': 'Fixture only', 'findings': []}
        with patch.object(dev, 'checks', side_effect=passing), \
                patch.object(dev, 'review', return_value=approved) as model, \
                patch.object(dev.GitHub, 'call') as remote, redirect_stdout(io.StringIO()):
            dev.main(argv)
            self.assertEqual(model.call_args.args[0]['check_evidence']['head'], self.base)
            with self.assertRaises(ValueError):
                dev.main(argv)
        self.assertEqual(model.call_count, 1)
        remote.assert_not_called()
        self.assertEqual(store.load()['review_calls'][dev.now()[:10]], 1)
        for switch in ('enabled', 'publish', 'automatic_merge'):
            cfg.write_text(json.dumps({**self.cfg, switch: True}))
            with self.subTest(switch=switch), self.assertRaises(ValueError):
                dev.main(argv)

    def test_trial_failed_checks_return_failure_without_remote_calls(self):
        task = self.root / 'task.json'
        task.write_text(json.dumps({'goal': 'Fixture', 'paths': ['tests/'], 'acceptance': 'Preserve tests'}))
        cfg = self.root / 'operator.json'
        cfg.write_text(json.dumps(self.cfg))
        original_umask = os.umask(0o077)
        try:
            with patch.object(dev, 'worker'), \
                 patch.object(dev, 'freeze', return_value=(self.base, {'eligible': True})), \
                 patch.object(dev, 'checks', return_value=False), \
                 patch.object(dev.GitHub, 'call') as remote, patch.object(dev, 'review') as review, \
                 redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                dev.main(['--config', str(cfg), 'trial', '--source', str(self.repo),
                          '--revision', self.base, '--task', str(task)])
            self.assertEqual(error.exception.code, 1)
            remote.assert_not_called()
            review.assert_not_called()
            result = next((Path(self.cfg['state_dir']) / 'trials').glob('*/result.json'))
            self.assertEqual(json.loads(result.read_text())['status'], 'failed')
        finally:
            os.umask(original_umask)


if __name__ == '__main__':
    unittest.main()
