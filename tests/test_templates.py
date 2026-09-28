from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.variables = yaml.safe_load(
            (ROOT / "ansible/inventory/group_vars/all.yml").read_text()
        )
        cls.lab = yaml.safe_load((ROOT / "config/lab.example.yml").read_text())
        cls.variables["lab_config"] = cls.lab
        cls.environment = Environment(
            loader=FileSystemLoader(str(ROOT)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        cls.environment.filters["ternary"] = lambda value, yes, no: yes if value else no

    def render(self, path: str, **values) -> str:
        return self.environment.get_template(path).render(**values)

    def resolve(self, value):
        if isinstance(value, str):
            return self.environment.from_string(value).render(lab_config=self.lab)
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        return value

    def test_ansible_yaml_is_valid(self):
        for path in (ROOT / "ansible").rglob("*.yml"):
            if "templates" not in path.parts:
                with self.subTest(path=path):
                    list(yaml.safe_load_all(path.read_text()))

    def test_netplan_for_every_guest_is_valid(self):
        host_networks = self.resolve(self.variables["host_networks"])
        for role in host_networks:
            with self.subTest(role=role):
                rendered = self.render(
                    "ansible/roles/common/templates/99-lab.yaml.j2",
                    host_networks=host_networks,
                    lab_config=self.lab,
                    lab_role=role,
                )
                model = yaml.safe_load(rendered)
                self.assertIn("enp1s0", model["network"]["ethernets"])

    def test_service_yaml_templates_are_valid(self):
        for path in [
            "ansible/roles/free5gc/templates/compose.lab.yaml.j2",
            "ansible/roles/ueransim/templates/gnb.yaml.j2",
            "ansible/roles/ueransim/templates/ue.yaml.j2",
        ]:
            with self.subTest(path=path):
                self.assertIsInstance(
                    yaml.safe_load(self.render(path, **self.variables)), dict
                )

    def test_subscriber_template_is_valid_json(self):
        import json

        rendered = self.render(
            "ansible/roles/free5gc/templates/subscriber.json.j2",
            **self.variables,
        )
        subscriber = json.loads(rendered)
        self.assertEqual(subscriber["ueId"], "imsi-208930000000001")

    def test_both_vinbero_configs_are_valid(self):
        for role, host, locator, next_hop in [
            ("tpe", "192.168.123.12", "fd10:1::/48", "2001:db8:100:10::12"),
            ("npe", "192.168.123.13", "fd10:2::/48", "2001:db8:100:10::13"),
        ]:
            with self.subTest(role=role):
                rendered = self.render(
                    "ansible/roles/vinbero_pe/templates/vinbero.yml.j2",
                    **self.variables,
                    pe_role=role,
                    ansible_host=host,
                )
                model = yaml.safe_load(rendered)
                self.assertEqual(model["bgp"]["locators"][0]["prefix"], locator)
                self.assertEqual(model["bgp"]["global"]["next_hop"], next_hop)
                self.assertEqual(model["bgp"]["global"]["mup_max_routes"], 4096)
                self.assertEqual(model["internal"]["bpf"]["device_mode"], "driver")
                self.assertEqual(
                    [peer["neighbor"] for peer in model["bgp"]["peers"]],
                    ["192.168.123.14"],
                )

        npe = yaml.safe_load(
            self.render(
                "ansible/roles/vinbero_pe/templates/vinbero.yml.j2",
                **self.variables,
                pe_role="npe",
                ansible_host="192.168.123.13",
            )
        )
        self.assertEqual(
            npe["bgp"]["vrf_bindings"][0]["mup_gtp4_source_prefix"],
            "fd10:2:100::/64",
        )
        self.assertEqual(
            npe["vrfs"]["entries"][0]["acs"], [{"interface": "enp3s0"}]
        )
        self.assertEqual(npe["vrfs"]["entries"][0]["members"], ["enp3s0"])

    def test_vinbero_bootstrap_has_correct_mup_roles(self):
        tpe = self.render(
            "ansible/roles/vinbero_pe/templates/bootstrap.sh.j2",
            **self.variables,
            pe_role="tpe",
        )
        self.assertIn("END_M_GTP4_E", tpe)
        self.assertIn("--route-type isd", tpe)
        self.assertIn("--args-offset 7", tpe)
        self.assertIn("--gtp-v4-src-addr 10.210.32.10", tpe)

        npe = self.render(
            "ansible/roles/vinbero_pe/templates/bootstrap.sh.j2",
            **self.variables,
            pe_role="npe",
        )
        self.assertIn("END_DT4", npe)
        self.assertIn("--route-type dsd", npe)
        self.assertIn("--segment-id2 65000", npe)
        self.assertIn("--segment-id4 1", npe)
        self.assertNotIn("ip route replace", npe)
        self.assertNotIn("ip -6 route replace", tpe)
        self.assertIn("ip vrf exec mup-dn ping", npe)

    def test_netplan_persists_mup_routes_and_tenant_membership(self):
        host_networks = self.resolve(self.variables['host_networks'])
        models = {}
        for role in ('tpe', 'npe'):
            models[role] = yaml.safe_load(self.render(
                'ansible/roles/common/templates/99-lab.yaml.j2',
                host_networks=host_networks, lab_config=self.lab, lab_role=role,
            ))['network']
        self.assertEqual(models['tpe']['ethernets']['enp3s0']['routes'], [
            {'to': self.lab['networks']['sr_underlay']['npe_locator'],
             'via': self.lab['networks']['sr_underlay']['npe_ipv6']},
        ])
        self.assertEqual(models['npe']['ethernets']['enp2s0']['routes'], [
            {'to': self.lab['networks']['sr_underlay']['tpe_locator'],
             'via': self.lab['networks']['sr_underlay']['tpe_ipv6']},
        ])
        self.assertEqual(models['npe']['vrfs']['mup-dn'], {
            'table': 100, 'interfaces': ['enp3s0'], 'optional': True,
        })
        self.assertEqual(models['npe']['ethernets']['enp3s0']['routes'], [
            {'to': self.lab['networks']['n6']['subnet'], 'scope': 'link', 'table': 100},
            {'to': self.lab['networks']['ue']['pool'],
             'via': self.lab['networks']['n6']['upf_ipv4'], 'table': 100, 'on-link': True},
        ])

    def test_npe_neighbor_probe_uses_configured_dn_and_tenant_vrf(self):
        from copy import deepcopy

        config = deepcopy(self.lab)
        config['networks']['n6']['dn_ipv4'] = '192.0.2.15'
        service = self.render(
            'ansible/roles/vinbero_pe/templates/vinbero-neighbor-refresh.service.j2',
            lab_config=config,
        )
        self.assertIn('ip vrf exec mup-dn /usr/bin/ping -n -c 1 -W 2 192.0.2.15', service)
        self.assertIn('TimeoutStartSec=5', service)
        self.assertNotIn('10.210.6.15', service)
        self.assertNotIn('uesimtun0', service)

    def test_npe_neighbor_timer_is_periodic_and_enabled_only_on_npe(self):
        timer = self.render(
            'ansible/roles/vinbero_pe/templates/vinbero-neighbor-refresh.timer.j2',
        )
        self.assertIn('OnActiveSec=1s', timer)
        self.assertIn('OnUnitActiveSec=15s', timer)
        self.assertIn('Unit=vinbero-neighbor-refresh.service', timer)
        self.assertIn('WantedBy=timers.target', timer)
        tasks = yaml.safe_load((ROOT / 'ansible/roles/vinbero_pe/tasks/main.yml').read_text())
        for name in ('Install bounded MUP PE (N6/Direct side) neighbor maintenance', 'Enable automatic DN neighbor recovery'):
            task = next(task for task in tasks if task.get('name') == name)
            self.assertEqual(task['when'], "pe_role == 'npe'")

    def test_pfcp_to_mup_configuration_is_valid(self):
        model = yaml.safe_load(
            self.render("ansible/templates/mup-config.yml.j2", **self.variables)
        )
        self.assertEqual(model["observer"]["interface"], "br-free5gc")
        self.assertEqual(model["observer"]["full_interval"], "5s")
        self.assertEqual(model["controller"]["observer_lease"], "15s")
        self.assertEqual(model["mup"]["session_rd"], "65000:10")
        self.assertEqual(model["mup"]["t1_route_target"], "65000:100")
        self.assertEqual(model["mup"]["t2_route_target"], "65000:200")
        self.assertEqual(model["policy"][0]["action"], "direct")


if __name__ == "__main__":
    unittest.main()
