from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def module_at(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config = module_at('lab_config', 'scripts/lab-config.py')
free5gc = module_at('lab_free5gc', 'ansible/filter_plugins/lab_free5gc.py')
network_definition = module_at('network_definition', 'scripts/network-definition.py')


class LabConfigTests(unittest.TestCase):
    def setUp(self):
        self.lab = yaml.safe_load((ROOT / 'config/lab.example.yml').read_text())

    def test_example_validates(self):
        config.validate(self.lab)

    def test_invalid_settings_are_rejected(self):
        for path, value in (
            ('host.dashboard.listen', '0.0.0.0:8787'),
            ('host.dashboard.tailscale.enabled', 'false'),
            ('host.ansible_user', 'user;id'),
            ('host.image_directory', '/'),
            ('host.image_directory', '/opt/../etc'),
            ('nodes.core.name', 'lab-ran'),
            ('nodes.core.management_ipv4', '192.168.123.100'),
            ('nodes.core.management_ipv4', '192.168.123.1'),
            ('networks.management.netmask', '255.255.0.0'),
            ('networks.n6.dn_ipv4', '10.210.6.13'),
            ('networks.ue.pool', '10.210.2.0/24'),
            ('mup.bgp_port', 1179),
            ('subscriber.mcc', 208),
            ('subscriber.sd', 'zzzzzz'),
        ):
            with self.subTest(path=path, value=value):
                lab = deepcopy(self.lab)
                parent = lab
                keys = path.split('.')
                for key in keys[:-1]:
                    parent = parent[key]
                parent[keys[-1]] = value
                with self.assertRaises(ValueError):
                    config.validate(lab)

    def test_unknown_or_missing_keys_fail(self):
        for mutate in (lambda lab: lab['host'].update(typo=True),
                       lambda lab: lab['host'].pop('ssh_private_key')):
            lab = deepcopy(self.lab)
            mutate(lab)
            with self.assertRaises(ValueError):
                config.validate(lab)

    def test_free5gc_render_reconciles_nondefault_network(self):
        self.lab['subscriber'].update(mcc='001', mnc='01', supi='imsi-001010000000001',
                                      sst=2, sd='000009', dnn='labdata')
        self.lab['networks']['ue']['pool'] = '10.70.0.0/16'
        self.lab['networks']['n3_core']['upf_ipv4'] = '10.210.32.20'
        config.validate(self.lab)
        source = {'configuration': {'snssaiInfos': [{'sNssai': {}, 'dnnInfos': [
            {'dnn': 'old', 'dns': {'ipv4': '8.8.8.8'}}]}],
            'userplaneInformation': {'upNodes': {'UPF': {
                'nodeID': 'upf.free5gc.org', 'interfaces': [{'interfaceType': 'N3'}]}}}}}
        original = yaml.safe_dump(source)
        rendered = free5gc.render_free5gc(original, 'smf', self.lab)
        result = yaml.safe_load(rendered)['configuration']
        self.assertEqual(result['plmnList'], [{'mcc': '001', 'mnc': '01'}])
        upf = result['userplaneInformation']['upNodes']['UPF']
        self.assertEqual(upf['nodeID'], 'upf.free5gc.org')  # N4 stays on Docker
        self.assertEqual(upf['interfaces'][0]['endpoints'], ['10.210.32.20'])
        self.assertEqual(upf['sNssaiUpfInfos'][0]['dnnUpfInfoList'], [
            {'dnn': 'labdata', 'pools': [{'cidr': '10.70.0.0/16'}]}])
        self.assertEqual(rendered, free5gc.render_free5gc(original, 'smf', self.lab))

    def test_free5gc_preserves_leading_zero_identifiers(self):
        source = 'configuration:\n  tac: 000001\n  sampleSd: 010203\n  nrfUri: http://nrf:8000\n'
        model = yaml.safe_load(free5gc.render_free5gc(source, 'nssf', self.lab))['configuration']
        self.assertEqual(model['tac'], '000001')
        self.assertEqual(model['sampleSd'], '010203')
        self.assertEqual(model['supportedPlmnList'], [{'mcc': '208', 'mnc': '93'}])

    def test_active_network_comparison_ignores_runtime_defaults(self):
        desired = '<network><name>example</name><bridge name="br-lab"/><forward mode="nat"/></network>'
        current = '<network connections="6"><name>example</name><uuid>auto</uuid><bridge name="br-lab" stp="on"/><forward mode="nat"><nat/></forward></network>'
        self.assertEqual(network_definition.signature(current), network_definition.signature(desired))
        self.assertNotEqual(network_definition.signature(current), network_definition.signature(desired.replace('br-lab', 'br-other')))


if __name__ == '__main__':
    unittest.main()
