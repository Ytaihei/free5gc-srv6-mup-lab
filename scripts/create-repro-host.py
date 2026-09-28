#!/usr/bin/env python3
"""Create a dedicated nested-KVM test host; never stop/delete existing guests."""

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def config_value(key):
    return run(str(ROOT / 'scripts/lab-config.py'), 'get', key, '--expand-path')


def validate(config):
    for name in (config['name'], config['user'], config['network']['name'], config['network']['bridge']):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', name):
            raise ValueError('Unsafe reproduction host/network/user name')
    if len(config['network']['bridge']) > 15:
        raise ValueError('Linux bridge name exceeds 15 characters')
    for key, minimum, maximum in [('memory_mib', 22528, 65536), ('vcpus', 2, 32), ('disk_gib', 220, 400)]:
        if type(config[key]) is not int or not minimum <= config[key] <= maximum:
            raise ValueError(f'Unsupported {key}')
    net = config['network']
    subnet = ipaddress.IPv4Network(net['subnet'])
    addresses = [ipaddress.IPv4Address(net[key]) for key in ('gateway', 'address')]
    if addresses[0] == addresses[1] or any(
        address not in subnet or address in (subnet.network_address, subnet.broadcast_address)
        for address in addresses
    ):
        raise ValueError('Invalid reproduction management addresses')
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', net['mac']):
        raise ValueError('Invalid MAC address')


def network_xml(config):
    net = config['network']
    root = ET.Element('network')
    ET.SubElement(root, 'name').text = net['name']
    ET.SubElement(root, 'forward', mode='nat')
    ET.SubElement(root, 'bridge', name=net['bridge'], stp='on', delay='0')
    ip = ET.SubElement(root, 'ip', address=net['gateway'], netmask=str(ipaddress.ip_network(net['subnet']).netmask))
    dhcp = ET.SubElement(ip, 'dhcp')
    ET.SubElement(dhcp, 'host', mac=net['mac'], name=config['name'], ip=net['address'])
    return ET.tostring(root, encoding='unicode')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/repro-host.yml',
                        help='Complete outer-host YAML profile (use unique names/subnet for a second run)')
    parser.add_argument('--create', action='store_true', help='Create and boot after all safety checks (default: read-only)')
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    validate(config)
    for command in ('virsh', 'virt-install', 'qemu-img', 'ssh-keygen', 'ip'):
        if not shutil.which(command):
            raise ValueError(f'Missing command: {command}')
    nested = [Path('/sys/module/kvm_intel/parameters/nested'), Path('/sys/module/kvm_amd/parameters/nested')]
    if not any(path.exists() and path.read_text().strip().lower() in ('y', '1') for path in nested):
        raise ValueError('Nested KVM is not enabled on the physical host')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise ValueError('/dev/kvm is not accessible')
    uri = config_value('host.libvirt_uri')
    virsh = ['virsh', '-c', uri]
    domains = run(*virsh, 'list', '--all', '--name').splitlines()
    if config['name'] in domains:
        raise ValueError('Reproduction VM already exists; inspect it manually, never overwrite')
    for role in ('core', 'ran', 'tpe', 'npe', 'mupc', 'dn'):
        name = config_value(f'nodes.{role}.name')
        if name in domains and run(*virsh, 'domstate', name) != 'shut off':
            raise ValueError(f'{name} is not shut off; gracefully stop the original lab first')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    available_mib = int(memory['MemAvailable'].split()[0]) // 1024
    if available_mib < config['memory_mib'] + 2048:
        raise ValueError(f'Insufficient available RAM: {available_mib} MiB')
    net = config['network']
    if net['name'] in run(*virsh, 'net-list', '--all', '--name').splitlines():
        raise ValueError('Reproduction network already exists; inspect manually, never redefine')
    if any(link['ifname'] == net['bridge'] for link in json.loads(run('ip', '-j', 'link'))):
        raise ValueError('Reproduction bridge already exists')
    subnet = ipaddress.ip_network(net['subnet'])
    # Include inactive libvirt networks as well as every IPv4 route table.
    for name in run(*virsh, 'net-list', '--all', '--name').splitlines():
        for ip in ET.fromstring(run(*virsh, 'net-dumpxml', name)).findall('ip'):
            if ip.get('family', 'ipv4') == 'ipv4':
                existing = ipaddress.ip_network(f"{ip.get('address')}/{ip.get('prefix') or ip.get('netmask')}", strict=False)
                if subnet.overlaps(existing):
                    raise ValueError(f'Management subnet overlaps libvirt network {name}')
    for route in json.loads(run('ip', '-j', '-4', 'route', 'show', 'table', 'all')):
        destination = route.get('dst', 'default')
        if destination != 'default' and subnet.overlaps(ipaddress.ip_network(destination, strict=False)):
            raise ValueError(f'Management subnet overlaps existing route {destination}')
    image_dir = Path(config_value('host.image_directory'))
    disk = image_dir / f"{config['name']}.qcow2"
    if disk.exists() or disk.is_symlink():
        raise ValueError(f'Refusing to overwrite {disk}')
    if shutil.disk_usage(image_dir).free < 220 * 1024**3:
        raise ValueError('At least 220 GiB free physical disk space is required')
    base = image_dir / 'noble-server-cloudimg-amd64.img'
    lock = yaml.safe_load((ROOT / 'config/versions.lock.yml').read_text())
    with base.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != lock['cloud_images']['noble']['sha256']:
        raise ValueError('Pristine Noble base does not match the locked checksum')
    public_key_path = config_value('host.ssh_public_key')
    run('ssh-keygen', '-l', '-f', public_key_path)
    public_key = ' '.join(Path(public_key_path).read_text().split()[:2])
    print(f"PASS: {config['name']}, {config['memory_mib']} MiB, {config['vcpus']} vCPU, {config['disk_gib']} GiB sparse", flush=True)
    if not args.create:
        print('Read-only check complete; pass --create to create the dedicated host.')
        return
    generated = ROOT / 'artifacts/repro/outer-host' / config['name']
    generated.mkdir(parents=True, exist_ok=False)
    (generated / 'network.xml').write_text(network_xml(config))
    user_data = {
        'hostname': config['name'], 'manage_etc_hosts': True,
        'users': [{'name': config['user'], 'groups': ['sudo'], 'shell': '/bin/bash',
                   'sudo': ['ALL=(ALL) NOPASSWD:ALL'], 'lock_passwd': True,
                   'ssh_authorized_keys': [public_key]}],
        'ssh_pwauth': False, 'disable_root': True,
        'package_update': True, 'packages': ['git'],
    }
    (generated / 'user-data.yml').write_text('#cloud-config\n' + yaml.safe_dump(user_data))
    (generated / 'meta-data.yml').write_text(yaml.safe_dump({'instance-id': config['name'], 'local-hostname': config['name']}))
    subprocess.run([*virsh, 'net-define', str(generated / 'network.xml')], check=True)
    subprocess.run([*virsh, 'net-start', net['name']], check=True)
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(base), str(disk), f"{config['disk_gib']}G"], check=True)
    shutil.chown(disk, group='kvm')
    disk.chmod(0o660)
    subprocess.run([
        'virt-install', '--connect', uri, '--virt-type', 'kvm', '--name', config['name'],
        '--memory', str(config['memory_mib']), '--vcpus', str(config['vcpus']), '--cpu', 'host-passthrough',
        '--os-variant', 'ubuntu24.04', '--import', '--graphics', 'none', '--noautoconsole',
        '--disk', f'path={disk},format=qcow2,bus=virtio',
        '--network', f"network={net['name']},model=virtio,mac={net['mac']}",
        '--cloud-init', f"user-data={generated / 'user-data.yml'},meta-data={generated / 'meta-data.yml'}",
    ], check=True)
    print(f"Connect: ssh {config['user']}@{net['address']}")
    print('No autostart or cleanup configured. Stop L2 guests and this VM before restoring the original lab.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'ERROR: {error}')
