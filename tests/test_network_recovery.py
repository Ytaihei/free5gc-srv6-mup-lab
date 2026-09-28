import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RemoteArgumentTests(unittest.TestCase):
    def test_remote_arguments_round_trip_without_shell_evaluation(self):
        # Override SSH with a local Bash shell. No real VM or network is used.
        script = '''source "$1/scripts/lab-lib.sh"
shift
lab_ssh() { shift; bash -c "$1"; }
lab_ssh_argv fixture python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@"
'''
        arguments = ['', 'a:b', "key' ; printf injected ; #", '$(printf injected)',
                     '`printf injected`', 'line one\nline two', ' space ',
                     '\\', '"', '*', ';', '|', '&', 'キー']
        result = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), *arguments],
                                env={**os.environ, 'LAB_CONFIG': str(ROOT / 'config/lab.example.yml')},
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), arguments)

    def test_all_session_control_calls_use_argv_helper_including_cleanup(self):
        for name, expected in [('test-baseline.sh', 2), ('test-mup.sh', 3),
                               ('test-observer-lease.sh', 1)]:
            with self.subTest(script=name):
                source = (ROOT / 'scripts' / name).read_text()
                calls = [line.strip() for line in source.splitlines()
                         if any('/usr/local/bin/mupctl ' + action in line
                                for action in ('resume', 'suppress'))]
                self.assertEqual(len(calls), expected)
                for call in calls:
                    self.assertTrue(call.startswith('lab_ssh_argv "$LAB_MUPC" /usr/local/bin/mupctl '))
                    self.assertIn('"$session_key"', call)


class NetworkRecoveryGateTests(unittest.TestCase):
    def test_requires_explicit_opt_in(self):
        result = subprocess.run(
            ['bash', str(ROOT / 'scripts/test-network-recovery.sh')],
            env={**os.environ, 'NETWORK_RECOVERY_ENABLE': '0'},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('reapplies/restarts PE networking', result.stderr)

    def test_each_missing_network_invariant_fails_before_mutation(self):
        # Substitute only SSH. Never touch a real VM from a unit test.
        mock = '''#!/usr/bin/env python3
import json
import os
import sys
command = sys.argv[-1]
checks = [
    ("ip -j -6 route show 'fd10:2::/48'", [{"gateway": "2001:db8:100:10::13", "dev": "enp3s0"}]),
    ("ip -j -6 route show 'fd10:1::/48'", [{"gateway": "2001:db8:100:10::12", "dev": "enp2s0"}]),
    ('ip -j link show enp3s0', [{"master": "mup-dn"}]),
    ("ip -j route show table 100 '10.210.6.0/24'", [{"scope": "link", "dev": "enp3s0"}]),
    ("ip -j route show table 100 '10.60.0.0/16'", [{"gateway": "10.210.6.10", "dev": "enp3s0"}]),
]
for index, (expected, result) in enumerate(checks):
    if command == expected:
        print(json.dumps([] if index == int(os.environ['MISSING_INVARIANT']) else result))
        sys.exit(0)
sys.exit('UNEXPECTED MUTATION: ' + command)
'''
        with tempfile.TemporaryDirectory() as directory:
            ssh = Path(directory) / 'ssh'
            ssh.write_text(mock)
            ssh.chmod(0o755)
            for missing in range(5):
                with self.subTest(missing=missing):
                    environment = {**os.environ, 'NETWORK_RECOVERY_ENABLE': '1',
                                   'LAB_CONFIG': str(ROOT / 'config/lab.example.yml'),
                                   'MISSING_INVARIANT': str(missing),
                                   'PATH': directory + os.pathsep + os.environ['PATH']}
                    result = subprocess.run(
                        ['bash', str(ROOT / 'scripts/test-network-recovery.sh')],
                        env=environment, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn('UNEXPECTED MUTATION', result.stderr)
