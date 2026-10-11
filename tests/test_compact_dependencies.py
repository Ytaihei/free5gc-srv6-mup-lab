"""Reviewed dependency floors preserve newer custom versions and fail closed."""
import json
import re
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compact_dependencies as deps
import compact_develop as develop


class DependencyTests(unittest.TestCase):
    def policy(self):
        return {'schema_version': 1, 'modules': {'example.com/module': 'v1.2.3'}}

    def test_only_present_older_modules_are_updated(self):
        for current, expected in [('v1.2.2', ['example.com/module@v1.2.3']),
                                  ('v1.2.3', []), ('v1.3.0', [])]:
            self.assertEqual(deps.upgrades({'example.com/module': {'Version': current}}, self.policy()), expected)
        self.assertEqual(deps.upgrades({}, self.policy()), [])

    def test_replacements_and_unordered_versions_require_review(self):
        for item in [{'Version': 'v1.0.0', 'Replace': {'Path': '../custom'}},
                     {'Version': 'v1.3.0-rc.1'}, {'Version': None}]:
            with self.assertRaises(ValueError):
                deps.upgrades({'example.com/module': item}, self.policy())

    def test_json_stream_and_duplicate_rejection(self):
        self.assertEqual(set(deps.read_modules(' {"Path":"a"}\n {"Path":"b"}')), {'a', 'b'})
        with self.assertRaises(ValueError):
            deps.read_modules('{"Path":"a"} {"Path":"a"}')

    def test_smf_floor_updates_webui_dependency_but_not_root_checkout(self):
        policy = json.loads((ROOT / 'config/compact-dependencies.json').read_text())
        name = 'github.com/free5gc/smf'
        self.assertEqual(deps.upgrades({name: {'Version': 'v1.4.3'}}, policy), [name + '@v1.4.5'])
        self.assertEqual(deps.upgrades({name: {'Main': True, 'Dir': '/scratch/src'}}, policy, '/scratch/src'), [])
        for item in ({'Main': True}, {'Main': True, 'Dir': '/scratch/src/old-smf'}):
            with self.assertRaisesRegex(ValueError, 'workspace-local'):
                deps.upgrades({name: item}, policy, '/scratch/src')
        with self.assertRaises(ValueError):
            deps.upgrades({name: {}}, policy)

    def test_http2_security_floor_covers_root_and_external_builds(self):
        policy = json.loads((ROOT / 'config/compact-dependencies.json').read_text())
        name = 'golang.org/x/net'
        floor = policy['modules'][name]
        self.assertGreaterEqual(deps.version(floor), deps.version('v0.60.0'))
        selected = re.search(r'^\s*golang.org/x/net\s+(v\S+)',
                             (ROOT / 'go.mod').read_text(), re.MULTILINE)
        self.assertIsNotNone(selected)
        self.assertGreaterEqual(deps.version(selected.group(1)), deps.version(floor))
        for old in ('v0.58.0', 'v0.59.0'):
            self.assertEqual(deps.upgrades({name: {'Version': old}}, policy), [name + '@' + floor])
        self.assertEqual(deps.upgrades({name: {'Version': floor}}, policy), [])
        major, minor, patch = deps.version(floor)
        newer = f'v{major}.{minor}.{patch + 1}'
        self.assertEqual(deps.upgrades({name: {'Version': newer}}, policy), [])
        with self.assertRaises(ValueError):
            deps.upgrades({name: {'Version': newer, 'Replace': {'Path': '../custom-net'}}}, policy)

    def test_license_bearing_afero_release_is_required_when_present(self):
        policy = json.loads((ROOT / 'config/compact-dependencies.json').read_text())
        name = 'github.com/fclairamb/afero-snd'
        target = policy['license_pins'][name]['to']
        self.assertEqual(deps.upgrades({name: {'Version': 'v0.1.0'}}, policy), [name + '@' + target])
        for current in (target, 'v0.2.0', 'v0.3.0'):
            self.assertEqual(deps.upgrades({name: {'Version': current}}, policy), [])
        for item in ({'Version': 'v0.1.1'}, {'Main': True},
                     {'Version': target, 'Replace': {'Path': '../old'}}):
            with self.assertRaises(ValueError):
                deps.upgrades({name: item}, policy)

    def test_failed_resolution_is_not_accepted(self):
        raw = json.dumps({'Path': 'example.com/module', 'Version': 'v1.0.0'})
        with patch.object(Path, 'read_text', return_value=json.dumps(self.policy())), \
             patch.object(deps.subprocess, 'check_output', return_value=raw), \
             patch.object(deps.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, 'did not meet'):
            deps.apply(Path('/scratch'), Path('/policy'))
        self.assertEqual(run.call_args_list[0].args[0], ['go', 'get', 'example.com/module@v1.2.3'])

    def test_every_external_go_build_applies_floors_after_copy(self):
        for name in ['vinbero', *('free5gc-' + n for n in develop.NF_NAMES)]:
            script = develop.build_script(name)
            self.assertLess(script.index('cp -a /source/.'), script.index('/run/dependencies.py'))
            self.assertLess(script.index('/run/dependencies.py'), script.index('go build'))
        for name in develop.OWN:
            self.assertNotIn('/run/dependencies.py', develop.build_script(name))

    def test_builder_does_not_ship_unused_npm_or_download_archives(self):
        recipe = (ROOT / 'containers/compact-builder.Dockerfile').read_text()
        self.assertIn('AS toolchains', recipe)
        self.assertIn('rm -rf /opt/node/lib/node_modules/npm', recipe)
        final = recipe.split('FROM ${BUILDER_BASE}\n')[1]
        self.assertNotIn('/opt/downloads', final)
        self.assertIn('COPY --from=toolchains /opt/node /opt/node', final)
        self.assertIn('"linux-libc-dev=${LIBC_HEADERS_VERSION}"', final)
        first = recipe.split('FROM ${BUILDER_BASE}\n')[0]
        self.assertIn('2fea2ebe77388df61566fcd43eb0070dbff2842c8786a0e7906a13e5f10c3e65', first)
        self.assertIn('rm /opt/go/src/crypto/x509/platform_root_key.pem', first)
        self.assertNotIn('rm /opt/go', final)


if __name__ == '__main__':
    unittest.main()
