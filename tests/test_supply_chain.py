import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('supply_chain_policy', ROOT / 'scripts/supply-chain-policy.py')
policy_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy_module)


class SupplyChainTests(unittest.TestCase):
    def setUp(self):
        self.policy = {'fail_severities': ['HIGH', 'CRITICAL'], 'allowed_package_licenses': ['MIT']}
        self.result = {'Target': 'go.mod', 'Packages': [{'Name': 'example', 'Version': 'v1', 'Licenses': ['MIT']}]}
        self.report = {'SchemaVersion': 2, 'Results': [self.result]}

    def test_good_inventory_passes(self):
        self.assertEqual(policy_module.evaluate(self.report, self.policy), [])

    def test_empty_inventory_fails(self):
        self.assertTrue(policy_module.evaluate({'SchemaVersion': 2}, self.policy))

    def test_high_without_fix_still_fails(self):
        self.result['Vulnerabilities'] = [{'Severity': 'HIGH', 'VulnerabilityID': 'CVE-test',
                                          'PkgName': 'example', 'InstalledVersion': 'v1'}]
        self.assertIn('fixed=unavailable', policy_module.evaluate(self.report, self.policy)[0])

    def test_unknown_license_fails(self):
        self.result['Packages'][0].pop('Licenses')
        self.assertTrue(policy_module.evaluate(self.report, self.policy))

    def test_binary_license_lookup_requires_exact_version(self):
        self.result['Packages'][0].pop('Licenses')
        self.assertTrue(policy_module.evaluate(self.report, self.policy, {('example', 'v2'): ['MIT']}))
        self.assertEqual(policy_module.evaluate(self.report, self.policy, {('example', 'v1'): ['MIT']}), [])

    def test_unreviewed_license_fails(self):
        self.result['Packages'][0]['Licenses'] = ['LicenseRef-unknown']
        self.assertTrue(policy_module.evaluate(self.report, self.policy))

    def test_license_exception_is_scoped_to_package_and_version(self):
        self.policy['license_exceptions'] = [{'name': 'host-tool', 'version': 'v1',
                                             'licenses': ['GPL-3.0-or-later'], 'reason': 'Host-only tool'}]
        package = self.result['Packages'][0]
        package.update(Name='host-tool', Licenses=['GPL-3.0-or-later'])
        self.assertEqual(policy_module.evaluate(self.report, self.policy), [])
        package['Name'] = 'linked-library'
        self.assertTrue(policy_module.evaluate(self.report, self.policy))
