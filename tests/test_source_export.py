import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_export', ROOT / 'scripts/export-source.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


class SourceExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test Author')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.hooksPath', '/dev/null')
        self.public_files = set(EXPORT.REQUIRED)
        self.commit('LICENSE', 'license fixture\n')
        self.commit('THIRD_PARTY_NOTICES.md', 'notice fixture\n')
        self.commit('scripts/export-source.py', '# exporter fixture\n')

    def git(self, *args):
        return EXPORT.git(self.repo, *args)

    def commit(self, path, content, public=True):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        if public:
            self.public_files.add(path)
        policy = self.repo / EXPORT.POLICY
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(json.dumps({'version': 1, 'files': sorted(self.public_files),
                                      'private_only': ['private-notes/']}))
        self.git('add', '--', EXPORT.POLICY)
        self.git('add', '--', path)
        self.git('commit', '-qm', 'test source')

    def test_export_is_repeatable_without_history_or_untracked_files(self):
        self.commit('README.md', 'historical private sample\n')
        self.commit('README.md', 'source snapshot\n')
        (self.repo / 'untracked.txt').write_text('must not be exported')
        first, second = self.root / 'first.tar.gz', self.root / 'second.tar.gz'
        result = EXPORT.export(self.repo, 'HEAD', first)
        self.git('commit', '--allow-empty', '-qm', 'different metadata, same tree')
        EXPORT.export(self.repo, 'HEAD', second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertFalse(result['publication_approved'])
        self.assertEqual(result['files'], 5)
        with tarfile.open(first) as archive:
            self.assertEqual([m.name for m in archive if m.isfile()], [
                'free5gc-srv6-mup-lab/LICENSE', 'free5gc-srv6-mup-lab/README.md',
                'free5gc-srv6-mup-lab/THIRD_PARTY_NOTICES.md',
                'free5gc-srv6-mup-lab/config/public-source.json',
                'free5gc-srv6-mup-lab/scripts/export-source.py',
            ])
            self.assertEqual(archive.extractfile('free5gc-srv6-mup-lab/README.md').read(), b'source snapshot\n')
            self.assertEqual(archive.pax_headers, {})
            for member in archive:
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
                self.assertEqual((member.uname, member.gname), ('', ''))

    def test_executable_mode_is_preserved(self):
        self.commit('scripts/example.sh', '#!/bin/sh\nexit 0\n')
        (self.repo / 'scripts/example.sh').chmod(0o755)
        self.git('add', 'scripts/example.sh')
        self.git('commit', '-qm', 'executable fixture')
        output = self.root / 'candidate.tar.gz'
        EXPORT.export(self.repo, 'HEAD', output)
        with tarfile.open(output) as archive:
            self.assertEqual(archive.getmember('free5gc-srv6-mup-lab/scripts/example.sh').mode, 0o755)

    def test_worktree_inventory_rejects_new_unclassified_files(self):
        (self.repo / 'new-file.md').write_text('requires an explicit decision')
        with self.assertRaisesRegex(ValueError, 'unclassified'):
            EXPORT.check_tree(self.repo)

    def test_private_paths_are_excluded_but_unclassified_paths_block_export(self):
        self.commit('private-notes/operations.md', 'private operational history', public=False)
        output = self.root / 'candidate.tar.gz'
        EXPORT.export(self.repo, 'HEAD', output)
        with tarfile.open(output) as archive:
            self.assertFalse(any('private-notes' in member.name for member in archive))
        self.commit('unclassified.md', 'new document', public=False)
        with self.assertRaisesRegex(ValueError, 'unclassified'):
            EXPORT.export(self.repo, 'HEAD', self.root / 'rejected.tar.gz')

    def test_policy_is_loaded_from_selected_commit_not_working_tree(self):
        self.commit('README.md', 'source\n')
        (self.repo / EXPORT.POLICY).write_text('{}')
        EXPORT.export(self.repo, 'HEAD', self.root / 'candidate.tar.gz')

    def test_private_identity_rejection_does_not_create_archive(self):
        self.commit('tests/example.py', 'name = "fixture-" + "operator"\n')
        output = self.root / 'candidate.tar.gz'
        with self.assertRaisesRegex(ValueError, 'matched value redacted'):
            EXPORT.export(self.repo, 'HEAD', output, ['fixture-operator'])
        self.assertFalse(output.exists())

    def test_existing_candidate_is_never_overwritten(self):
        self.commit('README.md', 'source\n')
        output = self.root / 'candidate.tar.gz'
        output.write_bytes(b'preserve me')
        with self.assertRaises(FileExistsError):
            EXPORT.export(self.repo, 'HEAD', output)
        self.assertEqual(output.read_bytes(), b'preserve me')

    def test_runtime_local_binary_and_key_members_are_rejected(self):
        for name, payload in [
            ('artifacts/evidence.txt', b'private runtime data'),
            ('lab.qcow2', b'not a source file'),
            ('host-ops/host.env', b'local configuration'),
            ('.env.production', b'credential file'),
            ('config/lab.local.yml', b'local configuration'),
            ('infra/cloud-init/generated/user-data', b'local initialization'),
            ('program', b'\x7fELF\x00'),
            ('notes.txt', b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----'),
        ]:
            with self.subTest(name=name):
                member = tarfile.TarInfo('free5gc-srv6-mup-lab/' + name)
                with self.assertRaises(ValueError):
                    EXPORT.check_member(member, payload)

    def test_missing_license_or_notices_prevents_export(self):
        self.git('rm', 'LICENSE')
        self.git('commit', '-qm', 'missing license fixture')
        with self.assertRaises(ValueError):
            EXPORT.export(self.repo, 'HEAD', self.root / 'candidate.tar.gz')

    def test_symlinks_are_not_followed(self):
        self.commit('README.md', 'source\n')
        (self.repo / 'link').symlink_to('/etc/passwd')
        self.git('add', 'link')
        self.git('commit', '-qm', 'symlink fixture')
        with self.assertRaises(ValueError):
            EXPORT.export(self.repo, 'HEAD', self.root / 'candidate.tar.gz')
