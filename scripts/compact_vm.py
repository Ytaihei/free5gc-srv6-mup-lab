"""Owned, non-destructive single-VM lifecycle. No access to legacy lab guests."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET

import yaml

from compact_config import ROOT, locks

VIRSH = ['virsh', '-c', 'qemu:///system']


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(args):
    return subprocess.check_output([str(a) for a in args], text=True).strip()


def write(path, content, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f'refusing symlink: {path}')
    path.write_text(content)
    path.chmod(mode)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare_base_image(base, spec):
    """Keep a new public distribution image readable after libvirt relabels it."""
    partial = base.with_suffix('.partial')
    if base.is_symlink() or partial.is_symlink():
        raise ValueError('refusing symlink cloud image cache')
    if not base.exists():
        run(['curl', '-fL', '--retry', '3', '-o', partial, spec['url']])
        if digest(partial) != spec['sha256']:
            raise ValueError('cloud image checksum mismatch')
        # libvirt can retain libvirt-qemu ownership on a shared backing image.
        # Do not depend on the caller's umask or on ownership being restored.
        shutil.chown(partial, group='kvm')
        partial.chmod(0o640)
        partial.rename(base)
    if not os.access(base, os.R_OK):
        raise ValueError('cached cloud image is unreadable; have its owner grant read access to the kvm group')
    if digest(base) != spec['sha256']:
        raise ValueError('cached cloud image checksum mismatch')


class VM:
    def __init__(self, config):
        self.config = config
        self.vm = config['vm']
        self.state = ROOT / '.lab' / self.vm['name']
        self.key = self.state / 'id_ed25519'
        self.disk = Path(self.vm['image_directory']) / (self.vm['name'] + '.qcow2')

    def ssh_args(self):
        return ['ssh', '-i', self.key, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
                '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=accept-new',
                '-o', f'UserKnownHostsFile={self.state / "known_hosts"}',
                f"{self.vm['user']}@{self.vm['address']}"]

    def ssh(self, command, **kwargs):
        return run([*self.ssh_args(), command], **kwargs)

    def owned(self):
        path = self.state / 'owner.json'
        if not path.exists():
            raise ValueError('no compact VM ownership record; refusing to adopt existing resources')
        owner = json.loads(path.read_text())
        for key in ('domain_uuid', 'network_uuid'):
            if str(uuid.UUID(owner[key])) != owner[key]:
                raise ValueError('invalid canonical ownership UUID')
        if owner['vm'] != self.vm:
            raise ValueError('VM settings differ from ownership record; use a separate named VM')
        domains = output([*VIRSH, 'list', '--all', '--name']).splitlines()
        if self.vm['name'] in domains:
            if output([*VIRSH, 'domuuid', self.vm['name']]) != owner['domain_uuid']:
                raise ValueError('VM ownership UUID mismatch')
            doc = ET.fromstring(output([*VIRSH, 'dumpxml', self.vm['name']]))
            disks = [node.get('file') for node in doc.findall('./devices/disk/source')]
            if str(self.disk) not in disks:
                raise ValueError('VM disk differs from ownership record')
        networks = output([*VIRSH, 'net-list', '--all', '--name']).splitlines()
        if self.vm['network'] in networks:
            if output([*VIRSH, 'net-uuid', self.vm['network']]) != owner['network_uuid']:
                raise ValueError('network ownership UUID mismatch')
        return owner

    def preflight(self):
        release = Path('/etc/os-release').read_text()
        if 'ID=ubuntu\n' not in release or 'VERSION_ID="24.04"' not in release:
            raise ValueError('compact v1 requires Ubuntu 24.04')
        for cmd in ('virsh', 'virt-install', 'qemu-img', 'ssh', 'ssh-keygen', 'curl', 'git', 'ip', 'systemctl', 'systemd-run'):
            if not shutil.which(cmd):
                raise ValueError(f'missing host dependency: {cmd}')
        run(['systemctl', '--user', 'show-environment'], stdout=subprocess.DEVNULL)
        if not os.access('/dev/kvm', os.R_OK | os.W_OK):
            raise ValueError('KVM is required; software emulation is not supported')
        if os.uname().machine != 'x86_64':
            raise ValueError('compact v1 supports x86-64 only')
        directory = self.disk.parent
        if not directory.is_dir() or not os.access(directory, os.W_OK):
            raise ValueError(f'image directory must exist and be writable: {directory}')
        domains = output([*VIRSH, 'list', '--all', '--name']).splitlines()
        networks = output([*VIRSH, 'net-list', '--all', '--name']).splitlines()
        if (self.state / 'owner.json').exists():
            self.owned()
        elif self.vm['name'] in domains or self.vm['network'] in networks or self.disk.exists():
            raise ValueError('compact name/disk collision; existing resources will not be adopted')
        running = self.vm['name'] in output([*VIRSH, 'list', '--name']).splitlines()
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        available = int(memory['MemAvailable'].split()[0]) // 1024
        if not running and available < self.vm['memory_mib'] + 2048:
            raise ValueError(f'insufficient available memory: {available} MiB (need VM + 2048 MiB reserve)')
        if shutil.disk_usage(directory).free < 30 * 1024**3:
            raise ValueError('at least 30 GiB free storage is required')
        subnet = ipaddress.ip_network(self.vm['subnet'])
        for name in networks:
            if name == self.vm['network']:
                continue
            for node in ET.fromstring(output([*VIRSH, 'net-dumpxml', name])).findall('ip'):
                if node.get('family', 'ipv4') == 'ipv4':
                    existing = ipaddress.ip_network(f"{node.get('address')}/{node.get('prefix') or node.get('netmask')}", strict=False)
                    if subnet.overlaps(existing):
                        raise ValueError(f'VM management subnet overlaps network {name}')
        for route in json.loads(output(['ip', '-j', '-4', 'route', 'show', 'table', 'all'])):
            if route.get('dev') == self.vm['bridge'] and self.vm['network'] in networks:
                continue
            dst = route.get('dst', 'default')
            if dst != 'default' and subnet.overlaps(ipaddress.ip_network(dst, strict=False)):
                raise ValueError(f'VM subnet overlaps host route {dst}')

    def ensure(self):
        self.preflight()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)
        if not (self.state / 'owner.json').exists():
            write(self.state / 'owner.json', json.dumps({'vm': self.vm, 'domain_uuid': str(uuid.uuid4()),
                                                       'network_uuid': str(uuid.uuid4())}, indent=2))
        owner = self.owned()
        if not self.key.exists():
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'srv6-mup-compact', '-f', self.key])
        public_key = ' '.join(self.key.with_suffix('.pub').read_text().split()[:2])
        image = locks()['cloud_images']['noble']
        base = self.disk.parent / 'noble-server-cloudimg-amd64.img'
        prepare_base_image(base, image)
        if self.vm['network'] not in output([*VIRSH, 'net-list', '--all', '--name']).splitlines():
            root = ET.Element('network')
            ET.SubElement(root, 'name').text = self.vm['network']
            ET.SubElement(root, 'uuid').text = owner['network_uuid']
            ET.SubElement(root, 'forward', mode='nat')
            ET.SubElement(root, 'bridge', name=self.vm['bridge'], stp='on', delay='0')
            node = ET.SubElement(root, 'ip', address=self.vm['gateway'], prefix=str(ipaddress.ip_network(self.vm['subnet']).prefixlen))
            dhcp = ET.SubElement(node, 'dhcp')
            ET.SubElement(dhcp, 'host', mac=self.vm['mac'], name=self.vm['name'], ip=self.vm['address'])
            write(self.state / 'network.xml', ET.tostring(root, encoding='unicode'))
            run([*VIRSH, 'net-define', self.state / 'network.xml'])
        if self.vm['network'] not in output([*VIRSH, 'net-list', '--name']).splitlines():
            run([*VIRSH, 'net-start', self.vm['network']])
        if self.vm['name'] not in output([*VIRSH, 'list', '--all', '--name']).splitlines():
            if self.disk.is_symlink():
                raise ValueError('refusing symlink disk')
            if not self.disk.exists():
                run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-F', 'qcow2', '-b', base, self.disk, f"{self.vm['disk_gib']}G"])
                shutil.chown(self.disk, group='kvm')
                self.disk.chmod(0o660)
            data = {'hostname': self.vm['name'], 'manage_etc_hosts': True,
                    'users': [{'name': self.vm['user'], 'shell': '/bin/bash', 'groups': ['sudo'],
                               'sudo': ['ALL=(ALL) NOPASSWD:ALL'], 'lock_passwd': True,
                               'ssh_authorized_keys': [public_key]}], 'ssh_pwauth': False,
                    'disable_root': True, 'package_update': True,
                    'packages': ['python3-yaml', 'python3-jinja2', 'qemu-guest-agent']}
            write(self.state / 'user-data.yml', '#cloud-config\n' + yaml.safe_dump(data))
            write(self.state / 'meta-data.yml', yaml.safe_dump({'instance-id': owner['domain_uuid'], 'local-hostname': self.vm['name']}))
            run(['virt-install', '--connect', 'qemu:///system', '--virt-type', 'kvm', '--name', self.vm['name'],
                 '--uuid', owner['domain_uuid'], '--memory', self.vm['memory_mib'], '--vcpus', self.vm['vcpus'],
                 '--cpu', 'host-passthrough', '--os-variant', 'ubuntu24.04', '--import', '--graphics', 'none', '--noautoconsole',
                 '--disk', f'path={self.disk},format=qcow2,bus=virtio',
                 '--network', f"network={self.vm['network']},model=virtio,mac={self.vm['mac']}",
                 '--cloud-init', f"user-data={self.state / 'user-data.yml'},meta-data={self.state / 'meta-data.yml'}"])
        elif output([*VIRSH, 'domstate', self.vm['name']]) == 'shut off':
            run([*VIRSH, 'start', self.vm['name']])
        print('Waiting for compact VM SSH and cloud-init...', flush=True)
        for _ in range(120):
            result = subprocess.run([str(a) for a in [*self.ssh_args(), 'true']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                break
            time.sleep(3)
        else:
            raise ValueError('VM SSH did not become available; VM is retained for diagnosis')
        self.ssh('sudo cloud-init status --wait')

    def sync(self):
        """Only reviewed source inventory; no .git, credentials, artifacts or local config."""
        files = json.loads((ROOT / 'config/public-source.json').read_text())['files']
        archive = self.state / 'source.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name in files:
                path = ROOT / name
                if path.is_symlink() or not path.is_file() or '..' in Path(name).parts or Path(name).is_absolute():
                    raise ValueError(f'unsafe source entry: {name}')
                tar.add(path, arcname=name, recursive=False)
        archive.chmod(0o600)
        self.ssh('sudo install -d -m 0755 /opt/srv6-mup-compact')
        with archive.open('rb') as stream:
            self.ssh('sudo tar -xzf - --no-same-owner -C /opt/srv6-mup-compact', stdin=stream)
        # Configuration is transferred separately and never enters a build context.
        guest_config = {'schema_version': 1, 'vm': self.config['vm'],
                        'dashboard_port': self.config['dashboard_port'],
                        'lab': {key: self.config['lab'][key] for key in ('networks', 'subscriber', 'mup')}}
        self.ssh('sudo sh -c "umask 077; tee /opt/srv6-mup-compact/config/compact.local.yml >/dev/null"',
                 input=yaml.safe_dump(guest_config), text=True)

    def runtime(self, *args):
        import shlex
        self.ssh('sudo python3 /opt/srv6-mup-compact/scripts/compact_runtime.py ' + shlex.join(args))

    def shell(self, component):
        import shlex
        from compact_compose import CORE_SERVICES, ROLE_SERVICES, EXTRA_SERVICES
        if component not in CORE_SERVICES + ROLE_SERVICES + EXTRA_SERVICES:
            raise ValueError('unknown service')
        self.owned()
        command = ['sudo', 'docker', 'compose', '-p', 'srv6-mup-compact', '-f',
                   '/opt/srv6-mup-compact/.lab/runtime/config/compose.yml',
                   'exec', component, 'sh']
        args = self.ssh_args()
        run([*args[:-1], '-t', args[-1], shlex.join(command)])

    def stop(self):
        self.owned()
        if output([*VIRSH, 'domstate', self.vm['name']]) != 'shut off':
            run([*VIRSH, 'shutdown', self.vm['name']])
            for _ in range(60):
                if output([*VIRSH, 'domstate', self.vm['name']]) == 'shut off':
                    break
                time.sleep(1)
            else:
                raise ValueError('graceful shutdown is still pending; no forced power-off was attempted')
        print('Compact VM is stopped. All disks and sources are retained.')

    def retirement_plan(self):
        owner = self.owned()
        if self.disk.is_symlink() or self.state.is_symlink() or self.disk.parent.is_symlink():
            raise ValueError('refusing symlink resource retirement')
        if not self.disk.is_file():
            raise ValueError('owned disk is missing; refusing incomplete retirement')
        for name in output([*VIRSH, 'list', '--all', '--name']).splitlines():
            doc = ET.fromstring(output([*VIRSH, 'dumpxml', name]))
            if name == self.vm['name']:
                disks = doc.findall('./devices/disk[@device="disk"]/source')
                if len(disks) != 1 or disks[0].get('file') != str(self.disk):
                    raise ValueError('extra or changed VM disks; refusing retirement')
                continue
            for source in doc.findall('./devices/interface/source'):
                if source.get('network') == self.vm['network'] or source.get('bridge') == self.vm['bridge']:
                    raise ValueError('another domain uses the owned network/bridge')
            if any(source.get('file') == str(self.disk) for source in doc.findall('./devices/disk//source')):
                raise ValueError('another domain references the owned disk')
        archive = self.state.parent / 'retired' / owner['domain_uuid']
        retired_disk = self.disk.with_name(self.vm['name'] + '-retired-' + owner['domain_uuid'] + '.qcow2')
        if archive.exists() or archive.is_symlink() or archive.parent.is_symlink() or retired_disk.exists() or retired_disk.is_symlink():
            raise ValueError('retirement destination already exists or is unsafe')
        return {'vm': self.vm['name'], 'network': self.vm['network'],
                'disk': str(self.disk), 'retained_disk': str(retired_disk), 'retained_state': str(archive)}

    def retire(self, confirmation=None):
        plan = self.retirement_plan()
        print(json.dumps(plan, indent=2), flush=True)
        if confirmation is None:
            print('Plan only. To remove owned libvirt definitions and archive data: '
                  f'./lab destroy --confirm {self.vm["name"]}')
            return
        if confirmation != self.vm['name']:
            raise ValueError('confirmation must exactly match the owned VM name')
        from compact_dashboard import Dashboard
        domains = output([*VIRSH, 'list', '--all', '--name']).splitlines()
        if self.vm['name'] in domains:
            write(self.state / 'retired-domain.xml', output([*VIRSH, 'dumpxml', self.vm['name']]))
        if self.vm['network'] in output([*VIRSH, 'net-list', '--all', '--name']).splitlines():
            write(self.state / 'retired-network.xml', output([*VIRSH, 'net-dumpxml', self.vm['network']]))
        # Validate the tunnel's ownership before stopping any VM resources.
        Dashboard(self).stop()
        if self.vm['name'] in domains:
            self.stop()
            run([*VIRSH, 'undefine', self.vm['name']])
        if self.vm['network'] in output([*VIRSH, 'net-list', '--name']).splitlines():
            run([*VIRSH, 'net-destroy', self.vm['network']])
        if self.vm['network'] in output([*VIRSH, 'net-list', '--all', '--name']).splitlines():
            run([*VIRSH, 'net-undefine', self.vm['network']])
        self.disk.rename(plan['retained_disk'])
        archive = Path(plan['retained_state'])
        archive.parent.mkdir(exist_ok=True, mode=0o700)
        self.state.rename(archive)
        print('Owned VM/network definitions removed. Disk, sources, captures and SSH state '
              'are retained at the paths above; no data was deleted.')
