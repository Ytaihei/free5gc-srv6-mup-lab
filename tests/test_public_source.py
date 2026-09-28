import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('public_source', ROOT / 'scripts/export-source.py')
SOURCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE)


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.policy = SOURCE.read_policy((ROOT / SOURCE.POLICY).read_text())

    def test_public_inventory_includes_tests_and_dashboard(self):
        files = self.policy['files']
        for required in ('tests/test_public_source.py', 'tests/test_reproducibility.py',
                         'internal/dashboard/web/index.html', 'Makefile'):
            self.assertIn(required, files)
        for name in files:
            self.assertFalse(SOURCE.is_private(name, self.policy), name)

    def test_private_and_legacy_components_are_not_distributed(self):
        for name in self.policy['files']:
            self.assertFalse(name.startswith(('host-ops/', 'controller/', 'pe_agent/',
                                               'ansible/roles/vpp_pe/')))

    def test_public_markdown_links_do_not_require_private_files(self):
        files = set(self.policy['files'])
        for name in sorted(files):
            if not name.endswith('.md'):
                continue
            for link in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', (ROOT / name).read_text()):
                parsed = urlsplit(link)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = ((ROOT / name).parent / unquote(parsed.path)).resolve()
                relative = str(target.relative_to(ROOT))
                with self.subTest(source=name, link=link):
                    self.assertTrue(relative in files or any(
                        item.startswith(relative.rstrip('/') + '/') for item in files))

    def test_privacy_check_covers_case_and_python_constant_concatenation(self):
        for name, payload in [
            ('tests/example.py', b'name = "fixture-" + "operator"'),
            ('tests/example.py', b'name = "fixture-" "operator"'),
            ('web/index.html', b'<h1>FIXTURE-OPERATOR</h1>'),
        ]:
            with self.subTest(name=name, payload=payload):
                with self.assertRaises(ValueError) as error:
                    SOURCE.check_privacy(name, payload, ['fixture-operator'])
                self.assertNotIn('fixture-operator', str(error.exception).casefold())
        SOURCE.check_privacy('README.md', b'generic lab examples', ['fixture-operator'])

    def test_invalid_policy_is_rejected(self):
        for change in (
            {'version': 2},
            {'files': self.policy['files'] + ['../outside']},
            {'files': self.policy['files'] + [self.policy['files'][0]]},
            {'private_only': ['tests/']},
            {'files': ['README.md']},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                SOURCE.read_policy(json.dumps({**self.policy, **change}))

    def test_tree_scan_checks_tests_and_rejects_extra_archive_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = sorted(SOURCE.REQUIRED | {'tests/identity.py'})
            policy = {'version': 1, 'files': names, 'private_only': ['private/']}
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('# source fixture\n')
            (root / SOURCE.POLICY).write_text(json.dumps(policy))
            SOURCE.check_tree(root)
            (root / 'tests/identity.py').write_text('name = "fixture-" + "operator"')
            with self.assertRaisesRegex(ValueError, 'Private identity'):
                SOURCE.check_tree(root, ['fixture-operator'])
            (root / 'private').mkdir()
            (root / 'private/local.txt').write_text('not for distribution')
            with self.assertRaisesRegex(ValueError, 'Unexpected archive paths'):
                SOURCE.check_tree(root)

    def test_private_denylist_requires_explicit_nonempty_values(self):
        self.assertEqual(SOURCE.read_denylist(None), [])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'identities.json'
            for content in ('[]', '[""]', '{}', '[1]'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    SOURCE.read_denylist(path)
            path.write_text('["fixture-operator"]')
            self.assertEqual(SOURCE.read_denylist(path), ['fixture-operator'])
