#!/usr/bin/env python3
"""Single-VM SRv6 MUP lab launcher. The six-VM reference is never modified."""
import argparse
import fcntl
import json
import shlex
import subprocess
import sys

from compact_config import load
from compact_vm import VM
from compact_dashboard import Dashboard
from compact_develop import COMPONENTS, source, host_rebuild


def start(vm, build=False, release_data=None):
    vm.ensure()
    vm.sync()
    if release_data is not None:
        # Explicit manifests stay private and never enter the source archive.
        destination = '/opt/srv6-mup-compact/.lab/runtime/release-request.json'
        vm.ssh('sudo install -d -m 0700 /opt/srv6-mup-compact/.lab/runtime')
        vm.ssh('sudo sh -c ' + shlex.quote('umask 077; tee ' + destination + ' >/dev/null'),
               input=json.dumps(release_data), text=True)
        vm.runtime('check-release')
    vm.runtime('prepare')
    if build:
        vm.runtime('build')
    elif release_data is not None:
        vm.runtime('install-release')
    vm.runtime('up')
    Dashboard(vm).start()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        epilog='Host setup before Python dependencies: ./lab deps [--apply]')
    parser.add_argument('--config', help='compact YAML overrides')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='read-only host checks')
    commands.add_parser('diagnose', help='read-only host/guest diagnostics without raw logs or configuration')
    commands.add_parser('builds', help='list private component build results and active image IDs')
    release = commands.add_parser('release', help='validate the pinned image release without changing the VM')
    release.add_argument('--manifest', help='explicit trusted release manifest')
    database = commands.add_parser('database', help='explicit compact-only database migration with retained source')
    database.add_argument('action', choices=['status', 'upgrade', 'recover'])
    audit = commands.add_parser('audit-images', help='preview private immutable-image supply-chain checks')
    audit.add_argument('--scan', action='store_true', help='save image archives and scan; never publish')
    for name, help_text in [('source', 'prepare an editable host source tree without overwriting changes'),
                            ('rebuild', 'build one component in the VM; activate with failure recovery'),
                            ('rollback', 'restore the previous component image without changing source')]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument('component', choices=sorted(COMPONENTS))
        if name == 'rebuild':
            command.add_argument('--local-builder', action='store_true',
                                 help='explicitly build the matching builder locally instead of pulling it')
            command.add_argument('--clean-runtime', action='store_true',
                                 help='NF only: replace inherited layers with the pinned compact base')
    up = commands.add_parser('up', help='create/provision/start the compact lab')
    images = up.add_mutually_exclusive_group()
    images.add_argument('--build', action='store_true', help='explicitly build local candidate images (before publication)')
    images.add_argument('--release', metavar='MANIFEST', help='install an explicit trusted pinned release; no live baseline replacement')
    commands.add_parser('down', help='graceful VM shutdown; retain data')
    destroy = commands.add_parser('destroy', help='preview removal of owned definitions; archive disks/data')
    destroy.add_argument('--confirm', metavar='VM_NAME', help='explicit exact-name confirmation to apply')
    commands.add_parser('status', help='runtime state')
    commands.add_parser('health', help='fail unless control plane, UE and both PEs are ready')
    commands.add_parser('evidence', help='list private guest-side test evidence summaries')
    commands.add_parser('render', help='print resolved configuration, excluding subscriber keys')
    dashboard = commands.add_parser('dashboard', help='manage the supervised read-only dashboard tunnel')
    dashboard.add_argument('action', nargs='?', default='start', choices=['start', 'stop', 'status'])
    scope = dashboard.add_mutually_exclusive_group()
    scope.add_argument('--tailnet', action='store_const', const=True, dest='tailnet', default=None,
                       help='explicitly add a binding to this host\'s Tailscale IPv4 address')
    scope.add_argument('--local', action='store_const', const=False, dest='tailnet', help='loopback only')
    test = commands.add_parser('test', help='explicitly disruptive lab tests')
    test.add_argument('scenario', choices=['one-call', 'all', 'baseline', 'mup', 'lease', 'restart', 'network', 'neighbor'])
    logs = commands.add_parser('logs')
    logs.add_argument('component')
    shell = commands.add_parser('shell', help='interactive shell in a component of the owned VM')
    shell.add_argument('component')
    args = parser.parse_args()
    release_data = None
    if args.command == 'release':
        from compact_release import read, fingerprint
        data = read(args.manifest, require_source=True)
        print(json.dumps({'version': data['release']['version'], 'manifest_sha256': fingerprint(data),
                          'builder_available': data['release']['builder'] is not None}, indent=2))
        return
    config = load(args.config)
    vm = VM(config)
    if args.command == 'up' and not args.build and (args.release or not (vm.state / 'owner.json').exists()):
        from compact_release import read
        # Fail before creating a VM when no reviewed/pinned release is bundled.
        release_data = read(args.release, require_source=True)
    lock = None
    if args.command in ('up', 'down', 'test', 'dashboard', 'destroy', 'source', 'rebuild', 'rollback', 'audit-images', 'database'):
        vm.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = vm.state / 'operation.lock'
        if lock_path.is_symlink():
            raise ValueError('refusing symlink operation lock')
        lock = lock_path.open('a')
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another compact lifecycle/test operation is running') from None
    if args.command == 'doctor':
        vm.preflight()
        print('PASS: compact host prerequisites and isolated VM resource checks')
    elif args.command == 'diagnose':
        from compact_diagnostics import host
        host(vm)
    elif args.command == 'audit-images':
        from compact_audit import audit
        audit(vm, args.scan)
    elif args.command == 'source':
        print(source(args.component))
    elif args.command == 'database':
        vm.owned()
        vm.sync()
        vm.runtime('database', args.action)
    elif args.command == 'rebuild':
        host_rebuild(vm, args.component, args.clean_runtime, args.local_builder)
    elif args.command == 'rollback':
        vm.owned()
        vm.sync()
        vm.runtime('rollback', args.component)
    elif args.command == 'render':
        safe = json.loads(json.dumps(config))
        for key in ('key', 'opc'):
            safe['lab']['subscriber'][key] = '<redacted>'
        print(json.dumps(safe, indent=2))
    elif args.command == 'up':
        start(vm, args.build, release_data)
    elif args.command == 'down':
        Dashboard(vm).stop()
        vm.stop()
    elif args.command == 'destroy':
        vm.retire(args.confirm)
    elif args.command == 'dashboard':
        tunnel = Dashboard(vm)
        if args.action == 'start':
            tunnel.start(args.tailnet)
        elif args.action == 'stop':
            tunnel.stop()
        else:
            tunnel.status()
    else:
        vm.owned()
        if args.command == 'shell':
            vm.shell(args.component)
        else:
            extra = [args.scenario] if args.command == 'test' else [args.component] if args.command == 'logs' else []
            vm.runtime(args.command, *extra)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'ERROR: {error}')
