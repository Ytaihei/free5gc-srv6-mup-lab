"""Check bilingual documentation without treating checksums as semantic review."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import unquote, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r'^```[^\n]*\n[\s\S]*?^```[ \t]*$', re.MULTILINE)
LINK = re.compile(r'\[[^\]]*\]\(([^\s)]+)\)')


def slug(text):
    return re.sub(r'[^\w\- ]', '', text.lower()).replace(' ', '-')


def anchors(text):
    result = set(re.findall(r'<a id="([^"]+)"', text))
    used = Counter()
    for heading in re.findall(r'^#{1,6} (.+)$', FENCE.sub('', text), re.MULTILINE):
        value = slug(heading)
        result.add(value if not used[value] else f'{value}-{used[value]}')
        used[value] += 1
    return result


class DocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / 'config/public-source.json').read_text())
        cls.public = set(cls.policy['files'])
        cls.records = []
        for name, visibility in [('config/documentation.json', 'public'),
                                 ('docs/documentation-private.json', 'private')]:
            path = ROOT / name
            if not path.exists() and visibility == 'private':
                continue
            manifest = json.loads(path.read_text())
            if manifest['version'] != 1:
                raise ValueError('Unsupported documentation manifest')
            cls.records.extend((record, visibility) for record in manifest['translations'])

    def test_all_markdown_documents_have_exactly_one_translation(self):
        if (ROOT / '.git').exists():
            paths = subprocess.check_output(
                ['git', '-C', str(ROOT), 'ls-files', '-z', '--cached', '--others',
                 '--exclude-standard'], text=True).split('\0')
        else:
            paths = [str(path.relative_to(ROOT)) for path in ROOT.rglob('*.md')]
        expected = {path for path in paths if path.endswith('.md') and not path.endswith('.ja.md')}
        recorded = [record['source'] for record, _ in self.records if record['kind'] == 'markdown']
        self.assertEqual(set(recorded), expected)
        self.assertEqual(len(recorded), len(set(recorded)))
        translations = [record['translation'] for record, _ in self.records]
        self.assertEqual(len(translations), len(set(translations)))
        self.assertEqual({p for p in paths if p.endswith('.ja.md')},
                         {p for p in translations if p.endswith('.ja.md')})

    def test_reviewed_hashes_and_visibility_match_both_languages(self):
        for record, visibility in self.records:
            for key in ('source', 'translation'):
                name = record[key]
                with self.subTest(path=name):
                    self.assertEqual((ROOT / name).resolve().is_relative_to(ROOT), True)
                    data = (ROOT / name).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), record[key + '_sha256'])
                    self.assertEqual(name in self.public, visibility == 'public')
                    if visibility == 'private':
                        self.assertTrue(any(name == entry or entry.endswith('/') and name.startswith(entry)
                                            for entry in self.policy['private_only']))
            translated = (ROOT / record['translation']).read_text()
            self.assertRegex(translated, r'[ぁ-んァ-ヶ一-龯]')
            self.assertNotIn('<!-- SOURCE-CODE:', translated)

    def test_markdown_navigation_and_sections_match(self):
        for record, _ in self.records:
            if record['kind'] != 'markdown':
                continue
            source = (ROOT / record['source']).read_text()
            translated = (ROOT / record['translation']).read_text()
            with self.subTest(source=record['source']):
                self.assertIn(f"[日本語]({Path(record['translation']).name})", source)
                self.assertIn(f"[English]({Path(record['source']).name})", translated)
                heading_levels = lambda text: re.findall(r'^(#{1,6}) ', FENCE.sub('', text), re.MULTILINE)
                self.assertEqual(heading_levels(source), heading_levels(translated))

    def test_executable_examples_and_inline_literals_are_preserved(self):
        for record, _ in self.records:
            if record['kind'] != 'markdown':
                continue
            source = (ROOT / record['source']).read_text()
            translated = (ROOT / record['translation']).read_text()
            with self.subTest(source=record['source']):
                self.assertEqual(FENCE.findall(source), FENCE.findall(translated))
                # Also protect the indented shell examples in contribution/host docs.
                indented = lambda text: re.findall(r'^    \S.*$', FENCE.sub('', text), re.MULTILINE)
                self.assertEqual(indented(source), indented(translated))
                literals = lambda text: set(re.findall(r'(?<!`)`([^`\n]+)`(?!`)', FENCE.sub('', text)))
                self.assertFalse(literals(source) - literals(translated),
                                 f'Missing literal values: {sorted(literals(source) - literals(translated))}')

    def test_all_local_links_and_fragments_resolve(self):
        for record, _ in self.records:
            for key in ('source', 'translation'):
                path = ROOT / record[key]
                if path.suffix != '.md':
                    continue
                for link in LINK.findall(path.read_text()):
                    parsed = urlsplit(link)
                    if parsed.scheme or parsed.netloc:
                        continue
                    target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
                    with self.subTest(source=record[key], link=link):
                        self.assertTrue(target.is_relative_to(ROOT))
                        self.assertTrue(target.exists())
                        if parsed.fragment:
                            self.assertIn(unquote(parsed.fragment), anchors(target.read_text()))

    def test_upstream_reference_links_are_preserved(self):
        for record, _ in self.records:
            if record['kind'] != 'markdown':
                continue
            urls = lambda text: {link for link in LINK.findall(text) if urlsplit(link).scheme}
            with self.subTest(source=record['source']):
                self.assertEqual(urls((ROOT / record['source']).read_text()),
                                 urls((ROOT / record['translation']).read_text()))

    def test_license_translation_is_explicitly_unofficial(self):
        records = [record for record, _ in self.records if record['kind'] == 'license']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['source'], 'LICENSE')
        translated = (ROOT / records[0]['translation']).read_text()
        self.assertIn('非公式', translated)
        self.assertIn('[英文LICENSE](LICENSE)', translated)
        for number in range(1, 10):
            self.assertIn(f'### {number}. ', translated)

    def test_feature_status_rows_and_evidence_match(self):
        def rows(name):
            text = (ROOT / 'docs' / name).read_text()
            return [[cell.strip() for cell in line.strip('|').split('|')]
                    for line in text.splitlines()
                    if re.match(r'^\| (?:CP|DP|V|O)-\d{2} \|', line)]

        source = rows('feature-status.md')
        translated = rows('feature-status.ja.md')
        self.assertTrue(source)
        self.assertEqual([row[0] for row in source], [row[0] for row in translated])
        self.assertEqual(len(source), len({row[0] for row in source}))
        implementation = {'Implemented': '実装済み', 'Partial': '一部実装',
                          'Not implemented': '未実装'}
        verification = {'Recorded lab test': '実機記録あり',
                        'Offline tests only': 'オフライン試験のみ', 'Unverified': '未検証'}
        for original, japanese in zip(source, translated):
            with self.subTest(feature=original[0]):
                self.assertEqual(len(original), 5)
                self.assertEqual(len(japanese), 5)
                self.assertIn(original[2], implementation)
                self.assertIn(original[3], verification)
                self.assertEqual(implementation[original[2]], japanese[2])
                self.assertEqual(verification[original[3]], japanese[3])
                evidence = LINK.findall(original[4])
                self.assertTrue(evidence, 'Each feature needs an evidence/boundary link')
                self.assertEqual(evidence, [link.replace('.ja.md', '.md')
                                            for link in LINK.findall(japanese[4])])

    def test_glossary_terms_match(self):
        def terms(name):
            result = []
            for line in (ROOT / 'docs' / name).read_text().splitlines():
                if not line.startswith('| '):
                    continue
                cells = [cell.strip() for cell in line.strip('|').split('|')]
                if len(cells) == 3 and cells[0] not in {'Term', '用語'}:
                    result.append(cells[0])
            return result

        source = terms('glossary.md')
        self.assertTrue(source)
        self.assertEqual(len(source), len(set(source)))
        self.assertEqual(source, terms('glossary.ja.md'))

    def test_pe_terminology_is_not_reintroduced_as_a_node_type(self):
        legacy = re.compile(r'(?<![A-Za-z0-9_])(?:T-PE|N-PE|TPE|NPE|MUP-GW|MUP GW|MUP-PE)(?![A-Za-z0-9_])')
        for record, _ in self.records:
            if record['kind'] != 'markdown':
                continue
            for key in ('source', 'translation'):
                name = record[key]
                text = (ROOT / name).read_text()
                with self.subTest(path=name):
                    # Only the glossary explains these legacy aliases/history.
                    if Path(name).name in ('glossary.md', 'glossary.ja.md'):
                        self.assertIn('| T-PE / N-PE |', text)
                        self.assertIn('| MUP-GW |', text)
                        continue
                    self.assertIsNone(legacy.search(text), 'Use MUP PE role labels; retain lowercase technical IDs')

    def test_issue_form_contract_is_unchanged(self):
        def contract(value):
            if isinstance(value, dict):
                return {key: contract(item) for key, item in value.items()
                        if key not in {'name', 'description', 'label', 'value'}}
            if isinstance(value, list):
                return [contract(item) for item in value]
            return value

        records = [record for record, _ in self.records if record['kind'] == 'issue_form']
        self.assertEqual(len(records), 1)
        for record in records:
            source = yaml.safe_load((ROOT / record['source']).read_text())
            translated = yaml.safe_load((ROOT / record['translation']).read_text())
            self.assertEqual(contract(source), contract(translated))
