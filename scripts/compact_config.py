"""Configuration shared by the compact host launcher and guest runtime."""
from __future__ import annotations

import copy
import importlib.util
import ipaddress
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def merge(base, override):
    result = copy.deepcopy(base)
    if not isinstance(override, dict):
        raise ValueError('configuration overrides must be mappings')
    for key, value in override.items():
        if key not in base:
            raise ValueError(f'unknown setting: {key}')
        if isinstance(base[key], dict):
            result[key] = merge(base[key], value)
        elif type(value) is not type(base[key]):
            raise ValueError(f'invalid type for {key}')
        else:
            result[key] = value
    return result


def load(path=None):
    defaults = yaml.safe_load((ROOT / 'config/compact.example.yml').read_text())
    if path is not None and not Path(path).is_file():
        raise ValueError(f'explicit configuration does not exist: {path}')
    path = Path(path) if path else ROOT / 'config/compact.local.yml'
    overrides = yaml.safe_load(path.read_text()) if path.exists() else {}
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError('compact configuration must be a mapping')
    logical = overrides.pop('lab', {})
    config = merge(defaults, overrides)
    if config['schema_version'] != 1:
        raise ValueError('compact schema_version must be 1')
    vm = config['vm']
    for key in ('name', 'user', 'network', 'bridge'):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', vm[key]):
            raise ValueError(f'invalid vm.{key}')
    if len(vm['bridge']) > 15:
        raise ValueError('bridge exceeds Linux interface name limit')
    for key, low, high in [('memory_mib', 4096, 65536), ('vcpus', 2, 32), ('disk_gib', 40, 400)]:
        if not low <= vm[key] <= high:
            raise ValueError(f'vm.{key} must be between {low} and {high}')
    if not 1024 <= config['dashboard_port'] <= 65535:
        raise ValueError('dashboard_port must be an unprivileged TCP port')
    directory = Path(vm['image_directory'])
    if (not directory.is_absolute() or '..' in directory.parts or len(directory.parts) < 4
            or not re.fullmatch(r'/[a-zA-Z0-9_./-]+', str(directory))):
        raise ValueError('image_directory must be a dedicated absolute path')
    net = ipaddress.IPv4Network(vm['subnet'])
    addresses = [ipaddress.IPv4Address(vm[k]) for k in ('gateway', 'address')]
    if len(set(addresses)) != 2 or any(a not in net or a in (net.network_address, net.broadcast_address) for a in addresses):
        raise ValueError('invalid VM management addresses')
    if not re.fullmatch(r'52:54:00:(?:[0-9a-f]{2}:){2}[0-9a-f]{2}', vm['mac']):
        raise ValueError('invalid VM MAC')
    base = yaml.safe_load((ROOT / 'config/lab.example.yml').read_text())
    if not isinstance(logical, dict) or set(logical) - {'networks', 'subscriber', 'mup'}:
        raise ValueError('lab overrides support networks, subscriber and mup only')
    config['lab'] = merge(base, logical)
    module('portable_lab_config', ROOT / 'scripts/lab-config.py').validate(config['lab'])
    # free5gc-compose's fixed SBI/N4 bridge is not part of the six-VM address
    # model, but is inside this same guest and must not overlap any lab segment.
    sbi = ipaddress.ip_network('10.100.200.0/24')
    if net.overlaps(sbi):
        raise ValueError('VM management overlaps fixed SBI/N4 bridge')
    for name, item in config['lab']['networks'].items():
        if name == 'management':
            subnet = ipaddress.ip_network(f"{item['gateway_ipv4']}/{item['prefix_length']}", strict=False)
        else:
            subnet = ipaddress.ip_network(item.get('subnet', item.get('pool')))
        if subnet.version == 4 and net.overlaps(subnet):
            raise ValueError(f'VM management overlaps logical {name}')
        if subnet.version == 4 and sbi.overlaps(subnet):
            raise ValueError(f'logical {name} overlaps fixed SBI/N4 bridge')
    return config


def locks():
    return yaml.safe_load((ROOT / 'config/versions.lock.yml').read_text())
