from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HostMemoryTests(unittest.TestCase):
    def check_memory(self, memory):
        return subprocess.run(
            ['bash', '-c', 'source "$1"; lab_require_host_memory "$2"', 'test',
             str(ROOT / 'scripts/host-memory.sh'), str(memory)],
            capture_output=True, text=True,
        )

    def test_refuses_four_gib_host(self):
        result = self.check_memory(3893)
        self.assertEqual(result.returncode, 1)
        self.assertIn('No VM startup', result.stderr)

    def test_boundary_and_invalid_values(self):
        for memory, expected in [(19455, 1), (19456, 0), (24576, 0), (31938, 0), ('invalid', 1)]:
            with self.subTest(memory=memory):
                self.assertEqual(self.check_memory(memory).returncode, expected)

    def test_budget_matches_fixed_vm_allocations(self):
        script = (ROOT / 'infra/libvirt/create-vms.sh').read_text()
        memory = [int(value) for value in re.findall(r'^create_vm .*?\)" (\d+) ', script, re.MULTILINE)]
        self.assertEqual(len(memory), 6)
        self.assertEqual(sum(memory), 17408)

    def test_direct_creation_and_start_cannot_bypass_guard(self):
        for file in ['infra/libvirt/create-vms.sh', 'infra/libvirt/lab-power.sh']:
            text = (ROOT / file).read_text()
            self.assertLess(text.index('lab_require_host_memory'), text.index('uri='))
