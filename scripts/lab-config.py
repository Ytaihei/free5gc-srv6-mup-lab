#!/usr/bin/env python3
"""Read and validate the portable lab configuration."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/lab.example.yml"
LOCAL_CONFIG = ROOT / "config/lab.local.yml"
NODE_KEYS = ("core", "ran", "tpe", "npe", "mupc", "dn")
NETWORK_KEYS = ("management", "n2", "n3_access", "n3_core", "sr_underlay", "n6", "ue")


def config_path() -> Path:
    configured = os.environ.get("LAB_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    if LOCAL_CONFIG.exists():
        return LOCAL_CONFIG.resolve()
    return DEFAULT_CONFIG


def lookup(value: Any, key_path: str) -> Any:
    for key in key_path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise KeyError(key_path)
        value = value[key]
    return value


def require(config: dict[str, Any], key_path: str) -> Any:
    try:
        value = lookup(config, key_path)
    except KeyError as error:
        raise ValueError(f"missing required setting: {key_path}") from error
    if value is None or value == "":
        raise ValueError(f"empty required setting: {key_path}")
    return value


def validate(config: dict[str, Any]) -> None:
    # The schema is deliberately closed: a misspelled or unsupported setting
    # must fail before any privileged provisioning command is invoked.
    example = yaml.safe_load(DEFAULT_CONFIG.read_text())
    def check_shape(actual: Any, expected: Any, prefix: str = "") -> None:
        if isinstance(expected, dict):
            if not isinstance(actual, dict) or set(actual) != set(expected):
                raise ValueError(f"{prefix or 'config'} must have exactly these keys: {', '.join(expected)}")
            for key in expected:
                check_shape(actual[key], expected[key], f"{prefix}.{key}".lstrip('.'))
        elif type(actual) is not type(expected):
            raise ValueError(f"{prefix} must be {type(expected).__name__}")
        elif isinstance(actual, str) and (not actual or any(ord(c) < 32 for c in actual)):
            raise ValueError(f"{prefix} must be a nonempty single-line string")
    check_shape(config, example)
    if config.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")

    def pattern(key: str, expression: str) -> None:
        if not re.fullmatch(expression, str(lookup(config, key))):
            raise ValueError(f"invalid {key}")

    def bounded(key: str, lower: int, upper: int) -> None:
        value = lookup(config, key)
        if not lower <= value <= upper:
            raise ValueError(f"{key} must be between {lower} and {upper}")

    # Current VM layout and peer APIs support the local system hypervisor,
    # fixed BGP port and fixed SID bit allocation, not arbitrary deployments.
    if config['host']['libvirt_uri'] != 'qemu:///system':
        raise ValueError('host.libvirt_uri must be qemu:///system')
    pattern('host.ansible_user', r'[a-z_][a-z0-9_-]{0,30}')
    pattern('host.dashboard.tailscale.interface', r'[a-zA-Z][a-zA-Z0-9_-]{0,14}')
    for key in ('image_directory', 'ssh_public_key', 'ssh_private_key'):
        pattern(f'host.{key}', r'(?:/|~/)[a-zA-Z0-9_./-]+')
        path = Path(config['host'][key]).expanduser()
        if '..' in path.parts or str(path) in ('/', str(Path.home()), '/opt', '/var', '/etc'):
            raise ValueError(f'host.{key} must be a specific safe path')
    if not config['host']['image_directory'].startswith('/'):
        raise ValueError('image_directory must be absolute (also used by the root installer)')
    if config['host']['ssh_public_key'] != config['host']['ssh_private_key'] + '.pub':
        raise ValueError('ssh_public_key must be ssh_private_key + .pub')
    pattern('host.dashboard.listen', r'127\.0\.0\.1:[0-9]{1,5}')
    if not 1 <= int(config['host']['dashboard']['listen'].split(':')[1]) <= 65535:
        raise ValueError('invalid dashboard listen port')
    pattern('host.dashboard.interval', r'[1-9][0-9]*s')
    for node in NODE_KEYS:
        pattern(f'nodes.{node}.name', r'[a-z][a-z0-9-]{0,62}')
    names = [config['nodes'][node]['name'] for node in NODE_KEYS]
    if len(set(names)) != len(names) or set(names) & {'all', 'ungrouped', 'core', 'ran', 'pe', 'controller', 'dn'}:
        raise ValueError('VM names must be unique and not collide with inventory groups')
    for network in NETWORK_KEYS[:-1]:
        pattern(f'networks.{network}.name', r'[a-z][a-z0-9-]{0,62}')
        pattern(f'networks.{network}.bridge', r'[a-z][a-z0-9_-]{0,14}')
    for field in ('name', 'bridge'):
        values = [config['networks'][key][field] for key in NETWORK_KEYS[:-1]]
        if len(set(values)) != len(values):
            raise ValueError(f'network {field}s must be unique')
    bounded('mup.bgp_port', 179, 179)
    bounded('mup.local_asn', 1, 4294967295)
    bounded('mup.direct_segment_asn', 1, 65535)
    bounded('mup.direct_segment_id', 1, 4294967295)
    bounded('mup.teid_prefix_length', 32, 32)
    bounded('networks.sr_underlay.mtu', 2000, 9000)
    for key in ('session_rd', 't1_route_target', 't2_route_target', 'tpe_isd_rd', 'npe_dsd_rd'):
        pattern(f'mup.{key}', r'[0-9]{1,5}:[0-9]{1,10}')
        asn, number = map(int, config['mup'][key].split(':'))
        if asn > 65535 or number > 4294967295:
            raise ValueError(f'invalid mup.{key}')
    for key, expression in (('mcc', r'[0-9]{3}'), ('mnc', r'[0-9]{2,3}'),
                            ('sd', r'[0-9a-fA-F]{6}'), ('amf', r'[0-9a-fA-F]{4}'),
                            ('imei', r'[0-9]{15}'), ('imei_sv', r'[0-9]{16}'),
                            ('dnn', r'[a-z][a-z0-9-]{0,62}')):
        pattern(f'subscriber.{key}', expression)
    bounded('subscriber.sst', 1, 255)
    sub = config['subscriber']
    if not sub['supi'].startswith('imsi-' + sub['mcc'] + sub['mnc']):
        raise ValueError('subscriber IMSI must start with its MCC/MNC')

    for key in (
        "host.libvirt_uri",
        "host.image_directory",
        "host.ssh_public_key",
        "host.ssh_private_key",
        "host.ansible_user",
        "host.dashboard.listen",
        "host.dashboard.interval",
        "host.dashboard.tailscale.interface",
        "host.dashboard.tailscale.port",
    ):
        require(config, key)

    management = ipaddress.ip_network(
        f"{require(config, 'networks.management.gateway_ipv4')}/"
        f"{require(config, 'networks.management.prefix_length')}",
        strict=False,
    )
    if management.version != 4:
        raise ValueError("management network must be IPv4")
    if str(management.netmask) != config['networks']['management']['netmask']:
        raise ValueError('management netmask and prefix_length disagree')
    for key in ("dhcp_start", "dhcp_end"):
        if ipaddress.ip_address(require(config, f"networks.management.{key}")) not in management:
            raise ValueError(f"networks.management.{key} is outside management subnet")
    management_addresses: list[ipaddress.IPv4Address] = []
    macs: list[str] = []
    for node in NODE_KEYS:
        require(config, f"nodes.{node}.name")
        address = ipaddress.ip_address(require(config, f"nodes.{node}.management_ipv4"))
        if address not in management:
            raise ValueError(f"nodes.{node}.management_ipv4 is outside management subnet")
        management_addresses.append(address)
        mac = str(require(config, f"nodes.{node}.management_mac")).lower()
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            raise ValueError(f"nodes.{node}.management_mac is invalid")
        macs.append(mac)
    if len(set(management_addresses)) != len(management_addresses):
        raise ValueError("management IPv4 addresses must be unique")
    if len(set(macs)) != len(macs):
        raise ValueError("management MAC addresses must be unique")
    mgmt = config['networks']['management']
    dhcp_start, dhcp_end = (ipaddress.ip_address(mgmt[k]) for k in ('dhcp_start', 'dhcp_end'))
    if dhcp_start > dhcp_end:
        raise ValueError('DHCP range is reversed')
    for address in [*management_addresses, ipaddress.ip_address(mgmt['gateway_ipv4'])]:
        if address in (management.network_address, management.broadcast_address) or dhcp_start <= address <= dhcp_end:
            raise ValueError('static management addresses must be usable and outside dynamic DHCP range')
    if ipaddress.ip_address(mgmt['gateway_ipv4']) in management_addresses:
        raise ValueError('management gateway cannot be a guest address')
    for key in ("nodes.tpe.n3_core_mac", "nodes.npe.n6_mac"):
        mac = str(require(config, key)).lower()
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            raise ValueError(f"{key} is invalid")

    for network in NETWORK_KEYS:
        if network != "ue":
            require(config, f"networks.{network}.name")
        subnet_key = "pool" if network == "ue" else "subnet"
        if network == "management":
            continue
        ipaddress.ip_network(require(config, f"networks.{network}.{subnet_key}"))

    address_memberships = (
        ("networks.n2", ("amf_ipv4", "gnb_ipv4")),
        ("networks.n3_access", ("gnb_ipv4", "tpe_ipv4")),
        ("networks.n3_core", ("upf_ipv4", "tpe_ipv4")),
        ("networks.sr_underlay", ("tpe_ipv6", "npe_ipv6")),
        ("networks.n6", ("upf_ipv4", "npe_ipv4", "dn_ipv4")),
    )
    for base, fields in address_memberships:
        subnet = ipaddress.ip_network(require(config, f"{base}.subnet"))
        addresses = [ipaddress.ip_address(require(config, f'{base}.{field}')) for field in fields]
        if len(set(addresses)) != len(addresses):
            raise ValueError(f'{base} addresses must be unique')
        for field in fields:
            address = ipaddress.ip_address(require(config, f"{base}.{field}"))
            if address not in subnet or (subnet.version == 4 and address in (subnet.network_address, subnet.broadcast_address)):
                raise ValueError(f"{base}.{field} is outside {subnet}")
    ipv4_subnets = [management, ipaddress.ip_network('10.100.200.0/24')]
    for key in ('n2', 'n3_access', 'n3_core', 'n6', 'ue'):
        network = config['networks'][key]
        subnet = ipaddress.ip_network(network['pool' if key == 'ue' else 'subnet'])
        if subnet.version != 4 or any(subnet.overlaps(other) for other in ipv4_subnets):
            raise ValueError(f'{key} must be IPv4 and not overlap another lab subnet (including internal Docker 10.100.200.0/24)')
        ipv4_subnets.append(subnet)

    durations: dict[str, int] = {}
    for key in ("mup.observer_interval", "mup.observer_lease"):
        match = re.fullmatch(r"([1-9][0-9]*)s", str(require(config, key)))
        if not match:
            raise ValueError(f"{key} must be a positive whole number of seconds")
        durations[key] = int(match.group(1))
    if durations["mup.observer_lease"] <= durations["mup.observer_interval"]:
        raise ValueError("observer_lease must exceed observer_interval")

    for key in (
        "networks.sr_underlay.tpe_locator",
        "networks.sr_underlay.npe_locator",
        "networks.sr_underlay.npe_gtp4_source_prefix",
        "networks.sr_underlay.tpe_trigger_prefix",
    ):
        if ipaddress.ip_network(require(config, key)).version != 6:
            raise ValueError(f"{key} must be IPv6")
    for key in (
        "networks.sr_underlay.tpe_service_sid",
        "networks.sr_underlay.npe_service_sid",
    ):
        if ipaddress.ip_address(require(config, key)).version != 6:
            raise ValueError(f"{key} must be IPv6")
    sr = config['networks']['sr_underlay']
    for name, length in (('tpe_locator', 48), ('npe_locator', 48), ('tpe_trigger_prefix', 56), ('npe_gtp4_source_prefix', 64)):
        if ipaddress.ip_network(sr[name]).prefixlen != length:
            raise ValueError(f'{name} requires /{length} for the implemented SID layout')
    tpe, npe = (ipaddress.ip_network(sr[k]) for k in ('tpe_locator', 'npe_locator'))
    if tpe.overlaps(npe) or any(ipaddress.ip_network(sr['subnet']).overlaps(loc) for loc in (tpe, npe)):
        raise ValueError('SR underlay and locators must not overlap')
    for role, locator in (('tpe', tpe), ('npe', npe)):
        if ipaddress.ip_address(sr[f'{role}_service_sid']) not in locator:
            raise ValueError(f'{role} service SID must be inside its locator')
    if not ipaddress.ip_network(sr['tpe_trigger_prefix']).subnet_of(tpe) or ipaddress.ip_address(sr['tpe_service_sid']) not in ipaddress.ip_network(sr['tpe_trigger_prefix']):
        raise ValueError('MUP PE (N3/Interwork side) SID must match the trigger inside its locator')

    for key, length in (("subscriber.key", 32), ("subscriber.opc", 32)):
        if not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", str(require(config, key))):
            raise ValueError(f"{key} must contain {length} hexadecimal characters")
    supi = str(require(config, "subscriber.supi"))
    if not supi.startswith("imsi-") or not re.fullmatch(
        r"[0-9]{5,15}", supi.removeprefix("imsi-")
    ):
        raise ValueError("subscriber.supi must be an IMSI")

    if ipaddress.ip_address(require(config, "mup.npe_dsd_address")).version != 4:
        raise ValueError("mup.npe_dsd_address must be IPv4")

    for key in ("host.dashboard.tailscale.port", "mup.bgp_port"):
        port = int(require(config, key))
        if not 0 < port < 65536:
            raise ValueError(f"{key} must be a valid TCP port")


def load() -> tuple[Path, dict[str, Any]]:
    path = config_path()
    try:
        document = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    validate(document)
    return path, document


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("path")
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("key_path")
    get_parser.add_argument("--expand-path", action="store_true")
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("template")
    subparsers.add_parser("json")
    subparsers.add_parser("inventory")
    args = parser.parse_args()

    try:
        path, config = load()
        if args.command == "validate":
            print(f"valid: {path}")
        elif args.command == "path":
            print(path)
        elif args.command == "get":
            value = lookup(config, args.key_path)
            if isinstance(value, (dict, list)):
                raise TypeError(f"{args.key_path} is not a scalar")
            if args.expand_path:
                value = Path(str(value)).expanduser()
            if isinstance(value, bool):
                print(str(value).lower())
            else:
                print(value)
        elif args.command == "render":
            template_path = Path(args.template)
            if template_path.is_absolute():
                loader_root = template_path.parent
                template_name = template_path.name
            else:
                loader_root = ROOT
                template_name = str(template_path)
            environment = Environment(
                loader=FileSystemLoader(str(loader_root)),
                undefined=StrictUndefined,
                keep_trailing_newline=True,
                autoescape=False,
            )
            print(environment.get_template(template_name).render(lab=config), end="")
        elif args.command == "json":
            print(json.dumps(config, sort_keys=True))
        elif args.command == "inventory":
            hostvars: dict[str, dict[str, Any]] = {}
            groups: dict[str, dict[str, Any]] = {}
            group_members = {
                "core": ("core",),
                "ran": ("ran",),
                "pe": ("tpe", "npe"),
                "controller": ("mupc",),
                "dn": ("dn",),
            }
            for role in NODE_KEYS:
                node = config["nodes"][role]
                variables: dict[str, Any] = {
                    "ansible_host": node["management_ipv4"],
                    "lab_role": role,
                }
                if role in ("tpe", "npe"):
                    variables["pe_role"] = role
                hostvars[node["name"]] = variables
            for group, roles in group_members.items():
                groups[group] = {
                    "hosts": [config["nodes"][role]["name"] for role in roles]
                }
            inventory = {
                "_meta": {"hostvars": hostvars},
                "all": {
                    "children": list(group_members),
                    "vars": {
                        "lab_config_file": str(path),
                        "ansible_user": config["host"]["ansible_user"],
                        "ansible_ssh_private_key_file": str(
                            Path(config["host"]["ssh_private_key"]).expanduser()
                        ),
                        "ansible_ssh_common_args": "-o StrictHostKeyChecking=accept-new",
                    },
                },
                **groups,
            }
            print(json.dumps(inventory, sort_keys=True))
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
