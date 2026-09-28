import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('history_export', ROOT / 'scripts/export-source.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


class PublicHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Example Author')
        self.git('config', 'user.email', 'author@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'tag.gpgsign', 'false')
        self.git('config', 'core.hooksPath', '/dev/null')
        self.files = sorted(EXPORT.REQUIRED | {'README.md'})
        for name in self.files:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# public fixture\n')
        (self.repo / EXPORT.POLICY).write_text(json.dumps({
            'version': 1, 'files': self.files, 'private_only': ['private/']}))
        self.commit()

    def git(self, *args):
        return EXPORT.git(self.repo, *args)

    def commit(self, message='public fixture'):
        self.git('add', '-A')
        self.git('commit', '--allow-empty', '-qm', message)

    def test_clean_history_is_distinct_from_publication_approval(self):
        self.git('tag', '-a', 'v0.1.0', '-m', 'public example')
        result = EXPORT.check_history(self.repo, ['fixture-operator'])
        self.assertTrue(result['history_matches_public_source_policy'])
        self.assertTrue(result['identity_denylist_applied'])
        self.assertFalse(result['publication_approved'])
        self.assertEqual(result['commits'], 1)
        self.assertEqual(result['annotated_tags'], 1)

    def test_clean_tip_does_not_hide_previous_identity(self):
        path = self.repo / 'README.md'
        path.write_text('fixture-operator\n')
        self.commit()
        path.write_text('public source\n')
        self.commit()
        EXPORT.check_tree(self.repo, ['fixture-operator'])
        result = EXPORT.check_history(self.repo, ['fixture-operator'])
        self.assertFalse(result['history_matches_public_source_policy'])
        self.assertNotIn('fixture-operator', json.dumps(result))

    def test_deleted_private_file_remains_nonpublic_history(self):
        (self.repo / 'private').mkdir()
        (self.repo / 'private/operations.md').write_text('private history')
        self.commit()
        self.git('rm', 'private/operations.md')
        self.commit()
        EXPORT.check_tree(self.repo)
        result = EXPORT.check_history(self.repo)
        self.assertEqual(result['issues']['commit_contains_nonpublic_or_missing_paths'], 1)

    def test_nondefault_branch_and_tag_history_are_checked(self):
        self.git('checkout', '-qb', 'archive')
        (self.repo / 'README.md').write_text('fixture-operator')
        self.commit()
        self.git('tag', 'old-snapshot')
        self.git('checkout', '-q', 'main')
        self.assertFalse(EXPORT.check_history(self.repo, ['fixture-operator'])['history_matches_public_source_policy'])
        self.git('branch', '-D', 'archive')  # disposable fixture branch only
        self.assertFalse(EXPORT.check_history(self.repo, ['fixture-operator'])['history_matches_public_source_policy'])

    def test_commit_ref_and_tag_metadata_are_checked(self):
        self.commit('fixture-operator in a commit message')
        self.git('branch', 'fixture-operator')
        self.git('tag', '-a', 'release', '-m', 'fixture-operator in a tag message')
        result = EXPORT.check_history(self.repo, ['fixture-operator'])
        self.assertGreaterEqual(result['issues']['private_or_nontext_metadata'], 3)
        self.assertNotIn('fixture-operator', json.dumps(result))

    def test_runtime_files_cannot_be_legitimized_by_allowlisting(self):
        (self.repo / 'example.pcap').write_text('not a source artifact')
        self.files.append('example.pcap')
        (self.repo / EXPORT.POLICY).write_text(json.dumps({
            'version': 1, 'files': self.files, 'private_only': []}))
        self.commit()
        result = EXPORT.check_history(self.repo)
        self.assertIn('forbidden_or_private_source_version', result['issues'])

    def test_noncommit_ref_is_rejected_without_echoing_its_name(self):
        blob = self.git('rev-parse', 'HEAD:README.md').decode().strip()
        self.git('update-ref', 'refs/tags/fixture-operator', blob)
        with self.assertRaisesRegex(ValueError, 'non-commit ref') as error:
            EXPORT.check_history(self.repo, ['fixture-operator'])
        self.assertNotIn('fixture-operator', str(error.exception))

    def test_shallow_history_is_rejected(self):
        clone = self.repo.parent / 'shallow'
        subprocess.run(['git', 'clone', '-q', '--depth=1', self.repo.as_uri(), str(clone)], check=True)
        with self.assertRaisesRegex(ValueError, 'Shallow'):
            EXPORT.check_history(clone)

    def test_replace_and_graft_masks_are_rejected(self):
        first = self.git('rev-parse', 'HEAD').decode().strip()
        self.commit('another public commit')
        second = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('replace', first, second)
        with self.assertRaisesRegex(ValueError, 'Replace'):
            EXPORT.check_history(self.repo)
        self.git('replace', '-d', first)
        (self.repo / '.git/info/grafts').write_text(second + '\n')
        with self.assertRaisesRegex(ValueError, 'Grafts'):
            EXPORT.check_history(self.repo)

    def test_git_root_is_required(self):
        with self.assertRaisesRegex(ValueError, 'top-level'):
            EXPORT.check_history(self.repo / 'scripts')


class PublicationPreventionTests(unittest.TestCase):
    def test_capture_and_local_artifacts_are_ignored(self):
        paths = ['example.pcap', 'trace.pcapng', 'capture.log', 'config/demo.local.yaml',
                 'database.sqlite3', 'disk.img', 'guest.vmdk', 'appliance.ova',
                 'backup.tar.gz', 'certificate.p12', 'private.pem', 'private.key']
        ignored = subprocess.check_output(['git', '-C', str(ROOT), 'check-ignore', '--no-index', '--stdin'],
                                          input='\n'.join(paths) + '\n', text=True) if (ROOT / '.git').exists() else None
        if ignored is None:
            # Validate the distributed ignore rules without needing Git history.
            with tempfile.TemporaryDirectory() as directory:
                subprocess.run(['git', 'init', '-q', directory], check=True)
                (Path(directory) / '.gitignore').write_bytes((ROOT / '.gitignore').read_bytes())
                ignored = subprocess.check_output(['git', '-C', directory, 'check-ignore', '--no-index', '--stdin'],
                                                  input='\n'.join(paths) + '\n', text=True)
        self.assertEqual(set(ignored.splitlines()), set(paths))

    def test_private_path_is_rejected_without_echoing_identity(self):
        with self.assertRaises(ValueError) as error:
            EXPORT.check_privacy('docs/fixture-operator.md', b'generic content', ['fixture-operator'])
        self.assertNotIn('fixture-operator', str(error.exception))

    def test_additional_runtime_and_local_formats_are_rejected(self):
        import tarfile
        for name in ('config/custom.local.yaml', 'database.sqlite3', 'cert.p12', 'cert.pfx'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                EXPORT.check_member(tarfile.TarInfo(name), b'placeholder')

    def test_workflows_do_not_persist_checkout_credentials(self):
        import yaml
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            workflow = yaml.safe_load(path.read_text())
            for job in workflow['jobs'].values():
                for step in job['steps']:
                    if step.get('uses', '').startswith('actions/checkout@'):
                        self.assertIs(step.get('with', {}).get('persist-credentials'), False, path.name)
