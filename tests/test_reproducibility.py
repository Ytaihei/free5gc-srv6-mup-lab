import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReproducibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = yaml.safe_load((ROOT / "config/versions.lock.yml").read_text())
        cls.ansible = yaml.safe_load(
            (ROOT / "ansible/inventory/group_vars/all.yml").read_text()
        )

    def test_go_lock_matches_build_configuration(self):
        go_version = next(
            line.split()[1]
            for line in (ROOT / "go.mod").read_text().splitlines()
            if line.startswith("go ")
        )
        self.assertEqual(self.lock["toolchains"]["go"]["version"], go_version)
        self.assertEqual(self.ansible["go_version"], go_version)
        self.assertEqual(
            self.lock["toolchains"]["go"]["sha256"],
            self.ansible["go_linux_amd64_sha256"],
        )
        self.assertEqual(
            self.lock["toolchains"]["go"]["url"],
            f"https://go.dev/dl/go{go_version}.linux-amd64.tar.gz",
        )

    def test_stdlib_license_metadata_matches_locked_compiler(self):
        policy = yaml.safe_load((ROOT / "config/supply-chain-policy.yml").read_text())
        entries = [entry for entry in policy["license_metadata"] if entry["name"] == "stdlib"]
        self.assertEqual(len(entries), 1)
        version = self.lock["toolchains"]["go"]["version"]
        self.assertEqual(entries[0]["version"], f"v{version}")
        self.assertEqual(entries[0]["licenses"], ["BSD-3-Clause"])
        self.assertEqual(entries[0]["source"], f"https://github.com/golang/go/blob/go{version}/LICENSE")

    def test_builds_cannot_download_an_unlocked_go_toolchain(self):
        self.assertIn('export GOTOOLCHAIN := local', (ROOT / 'Makefile').read_text())
        for script in ('install-dashboard.sh', 'supply-chain.sh'):
            self.assertIn('export GOTOOLCHAIN=local', (ROOT / 'scripts' / script).read_text())
        for role in ('mup_binaries', 'vinbero_pe'):
            tasks = yaml.safe_load((ROOT / f'ansible/roles/{role}/tasks/main.yml').read_text())
            go_tasks = [task for task in tasks if '/bin/go ' in task.get('ansible.builtin.command', '')]
            self.assertGreater(len(go_tasks), 0)
            for task in go_tasks:
                with self.subTest(role=role, task=task['name']):
                    self.assertEqual(task.get('environment', {}).get('GOTOOLCHAIN'), 'local')

    def test_mixed_gobgp_baseline_is_explicit(self):
        module = (ROOT / 'go.mod').read_text()
        patch = (ROOT / 'third_party/vinbero/patches/0001-gobgp-v4.8-mup-draft01.patch').read_text()
        scope = (ROOT / 'docs/research-scope.md').read_text()
        self.assertIn('github.com/osrg/gobgp/v4 v4.9.0', module)
        self.assertIn('+\tgithub.com/osrg/gobgp/v4 v4.8.0', patch)
        self.assertIn('GoBGP v4.9.0 in MUP-C and v4.8.0', scope)

    def test_original_go_tools_do_not_depend_on_late_gcc_installation(self):
        tasks = yaml.safe_load((ROOT / 'ansible/roles/mup_binaries/tasks/main.yml').read_text())
        selected = [task for task in tasks if task['name'] in (
            'Run MUP Go unit tests before installation', 'Build MUP Go binaries',
        )]
        self.assertEqual(len(selected), 2)
        for task in selected:
            self.assertEqual(task['environment']['CGO_ENABLED'], '0')
        self.assertIn('export CGO_ENABLED := 0', (ROOT / 'Makefile').read_text())
        for script in ('install-dashboard.sh', 'supply-chain.sh'):
            self.assertIn('CGO_ENABLED=0', (ROOT / 'scripts' / script).read_text())

    def test_source_locks_match_ansible(self):
        mappings = {
            "vinbero": "vinbero_commit",
            "free5gc_compose": "free5gc_compose_commit",
            "gtp5g": "gtp5g_commit",
            "ueransim": "ueransim_commit",
        }
        for source, ansible_key in mappings.items():
            with self.subTest(source=source):
                commit = self.lock["sources"][source]["commit"]
                self.assertRegex(commit, r"^[0-9a-f]{40}$")
                self.assertEqual(commit, self.ansible[ansible_key])

    def test_container_digests_match_ansible(self):
        images = self.lock["containers"]["images"]
        compose_override = (
            ROOT / "ansible/roles/free5gc/templates/compose.lab.yaml.j2"
        ).read_text()
        self.assertEqual(self.lock["containers"]["status"], "digest-pinned")
        self.assertEqual(images, self.ansible["container_images"])
        for name, image in images.items():
            self.assertRegex(image, r"^[^@]+@sha256:[0-9a-f]{64}$")
            self.assertIn(f"container_images.{name}", compose_override)

    def test_cloud_images_are_immutable(self):
        create_script = (ROOT / "infra/libvirt/create-vms.sh").read_text()
        self.assertIn("config/versions.lock.yml", (ROOT / "scripts/lock-value.py").read_text())
        self.assertIn("cloud_images.jammy.sha256", create_script)
        self.assertIn("cloud_images.noble.sha256", create_script)
        for image in self.lock["cloud_images"].values():
            self.assertNotIn("/current/", image["url"])
            self.assertRegex(image["sha256"], r"^[0-9a-f]{64}$")

    def test_lab_runtime_does_not_depend_on_host_ops(self):
        for relative in ["ansible", "configs", "infra"]:
            for path in (ROOT / relative).rglob("*"):
                if path.is_file() and '__pycache__' not in path.parts:
                    self.assertNotIn("host-ops", path.read_text())

    def test_core_restart_reconnects_ran_after_observer_is_ready(self):
        handler = (ROOT / "ansible/roles/free5gc/handlers/main.yml").read_text()
        observer = (ROOT / "ansible/roles/pfcp_observer/tasks/main.yml").read_text()
        verify_index = observer.index("Verify passive PFCP observer is active")
        reconnect_index = observer.index("Reconnect RAN and UE after a free5GC restart")
        self.assertIn("free5gc_restarted: true", handler)
        self.assertLess(verify_index, reconnect_index)
        self.assertIn("delegate_to", observer[reconnect_index:])
        self.assertIn('observer_service_start.changed', observer)
        self.assertIn('observer_restarted | default(false)', observer)
        self.assertIn("item + '.service' in ran_services.ansible_facts.services", observer)

    def test_portable_config_drives_inventory_and_network_rendering(self):
        lab = yaml.safe_load((ROOT / "config/lab.example.yml").read_text())
        lab["nodes"]["core"]["management_ipv4"] = "192.168.123.20"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml") as config_file:
            yaml.safe_dump(lab, config_file)
            config_file.flush()
            environment = {**os.environ, "LAB_CONFIG": config_file.name}
            inventory = json.loads(
                subprocess.check_output(
                    [ROOT / "scripts/lab-config.py", "inventory"],
                    env=environment,
                    text=True,
                )
            )
            rendered_network = subprocess.check_output(
                [
                    ROOT / "scripts/lab-config.py",
                    "render",
                    "infra/libvirt/networks/lab-mgmt.xml.j2",
                ],
                env=environment,
                text=True,
            )
        self.assertEqual(
            inventory["_meta"]["hostvars"]["lab-core"]["ansible_host"],
            "192.168.123.20",
        )
        self.assertIn("ip='192.168.123.20'", rendered_network)

    def test_active_scripts_do_not_embed_management_addresses(self):
        for path in [
            *sorted((ROOT / "scripts").glob("*.sh")),
            *sorted((ROOT / "infra/libvirt").glob("*.sh")),
        ]:
            with self.subTest(path=path):
                self.assertNotIn("192.168.123.", path.read_text())


if __name__ == "__main__":
    unittest.main()
