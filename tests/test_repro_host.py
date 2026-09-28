import copy
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('repro_host', ROOT / 'scripts/create-repro-host.py')
REPRO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPRO)


class ReproHostTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((ROOT / 'config/repro-host.yml').read_text())

    def test_profile_and_dedicated_network(self):
        REPRO.validate(self.config)
        xml = ET.fromstring(REPRO.network_xml(self.config))
        self.assertEqual(xml.find('forward').get('mode'), 'nat')
        self.assertEqual(xml.find('ip/dhcp/host').get('ip'), self.config['network']['address'])

    def test_reject_invalid_names_and_capacity(self):
        for key, value in [('name', '../original'), ('user', 'a,b'), ('memory_mib', 8192), ('vcpus', True), ('disk_gib', 100)]:
            config = copy.deepcopy(self.config)
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                REPRO.validate(config)

    def test_reject_invalid_network(self):
        for key, value in [('bridge', 'a' * 16), ('address', '192.168.124.1'), ('address', '192.168.125.10'), ('address', '192.168.124.255'), ('mac', 'bad')]:
            config = copy.deepcopy(self.config)
            config['network'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                REPRO.validate(config)
