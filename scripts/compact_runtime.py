#!/usr/bin/env python3
"""Privileged runtime inside the dedicated compact VM, never the physical host."""
from __future__ import annotations

import argparse
import hashlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import shutil
import time
import urllib.request
import uuid
from datetime import datetime, timezone

from compact_config import ROOT, load, locks
from compact_vm import digest, output, run, write
from compact_compose import render, candidate_selection, CORE_SERVICES, ROLE_SERVICES, EXTRA_SERVICES
from compact_evidence import Capture, bpf_state, verify_packets, verify_control

STATE = ROOT / '.lab/runtime'
SOURCES = ROOT / '.lab/sources'
GENERATED = STATE / 'config'


def compose(*args, capture=False):
    command = ['docker', 'compose', '-p', 'srv6-mup-compact', '-f', GENERATED / 'compose.yml', *args]
    return output(command) if capture else run(command)


def execute(service, *args, capture=True):
    if service not in CORE_SERVICES + ROLE_SERVICES + EXTRA_SERVICES:
        raise ValueError(f'unknown service: {service}')
    return compose('exec', '-T', service, *args, capture=capture)


def wait_for(description, check, timeout=60):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            value = check()
            if value:
                print(f'PASS: {description}', flush=True)
                return value
        except (subprocess.CalledProcessError, ValueError, OSError) as error:
            last = error
        time.sleep(1)
    raise ValueError(f'timed out: {description}; last error: {last}')


def controller(command='status', *args):
    return json.loads(execute('mupc', 'mupctl', '--server',
        f"http://{load()['lab']['nodes']['mupc']['management_ipv4']}:9443", command, *args))


def build(config):
    from compact_develop import DEV, OWN, atomic, snapshot, build_payload
    if (DEV / 'pending.json').exists():
        raise ValueError('unfinished activation; run up to recover before building a new baseline')
    prepare(config)
    prepare_build_tools()
    staging = STATE / 'bootstrap' / uuid.uuid4().hex
    bin_dir = staging / 'image/bin'
    bin_dir.mkdir(parents=True)
    records = {}
    for name in (*OWN, 'vinbero', 'ueransim'):
        source = ROOT if name in OWN else checkout(name)
        if name == 'vinbero':
            patch = ROOT / 'third_party/vinbero/patches/0001-gobgp-v4.8-mup-draft01.patch'
            if 'github.com/osrg/gobgp/v4 v4.8.0' not in (source / 'go.mod').read_text():
                run(['git', 'apply', '--check', patch], cwd=source)
                run(['git', 'apply', patch], cwd=source)
        path = staging / 'common' / name
        snapshot(name, path / 'source.tar', root=source)
        print('Building preserved common-runtime input: ' + name, flush=True)
        record = build_payload(name, path, local_builder=True)
        for filename in record['artifacts']:
            if '/' in filename or (bin_dir / filename).exists():
                raise ValueError('unexpected/duplicate common-runtime artifact')
            shutil.copy2(path / 'output' / filename, bin_dir / filename)
        records[name] = str((path / 'payload.json').relative_to(STATE))
    fingerprint = hashlib.sha256()
    for path in sorted(bin_dir.iterdir()):
        fingerprint.update(path.name.encode() + bytes.fromhex(digest(path)))
    dockerfile = staging / 'image/Dockerfile'
    shutil.copy2(ROOT / 'containers/compact-runtime.Dockerfile', dockerfile)
    fingerprint.update(dockerfile.read_bytes())
    base = locks()['compact']['runtime_base']
    fingerprint.update(base.encode())
    image = 'srv6-mup-compact-local:' + fingerprint.hexdigest()[:24]
    run(['docker', 'build', '--build-arg', f'RUNTIME_BASE={base}', '-f', dockerfile, '-t', image, bin_dir.parent])
    manifest = {'image': image, 'image_id': output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image]),
                'binary_sha256': {p.name: digest(p) for p in sorted(bin_dir.iterdir())},
                'kernel': os.uname().release, 'dashboard_snapshot_protocol': 1,
                'common_build_records': records,
                'common_runtime_recipe': str(dockerfile.relative_to(STATE)),
                'common_runtime_recipe_sha256': digest(dockerfile),
                'publication': 'not-reviewed-local-candidate'}
    # Preserve licenses alongside source inputs, outside the runtime image.
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, staging / name)
    atomic(staging / 'common-build.json', manifest)
    complete_candidate(config, staging, manifest)
    print('Local common runtime and all 11 clean-runtime NFs built; no images have been published.', flush=True)


def complete_candidate(config, staging, manifest):
    from compact_develop import atomic, overrides
    manifest.update(image_set_schema=1, **build_nf_images(staging))
    upstream = checkout('free5gc_compose')
    # Validation must not rewrite live configs/certificates. Their absolute
    # mounts are rendered again by up, never moved from this staging directory.
    preview = staging / 'config'
    render(config, upstream, preview, manifest['image_id'], candidate_selection(manifest, overrides()),
           database_selection(create=False))
    run(['docker', 'compose', '-p', 'srv6-mup-compact', '-f', preview / 'compose.yml', 'config', '--quiet'])
    atomic(staging / 'candidate.json', manifest)
    atomic(STATE / 'candidate.json', manifest)


def build_nf_images(staging):
    from compact_develop import NF_NAMES, snapshot, build_image
    images, records = {}, {}
    for nf in NF_NAMES:
        name = 'free5gc-' + nf
        source = checkout(name)
        if output(['git', '-C', source, 'status', '--porcelain']):
            raise ValueError('bootstrap NF checkout has edits; use source/rebuild for customization')
        path = staging / 'nfs' / uuid.uuid4().hex
        snapshot(name, path / 'source.tar', root=source)
        print(f'Building clean bootstrap NF: {name}', flush=True)
        record = build_image(name, path, clean_runtime=True, local_builder=True)
        images[name] = record['image_id']
        records[name] = str((path / 'build.json').relative_to(STATE))
    return {'nf_images': images, 'nf_build_records': records}


def webui(path, body=None, token=None, method=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Token'] = token
    container = compose('ps', '-q', 'free5gc-webui', capture=True)
    networks = json.loads(output(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', container]))
    address = networks['srv6-mup-compact_privnet']['IPAddress']
    request = urllib.request.Request(f'http://{address}:5000/api/' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        data = response.read()
        return json.loads(data) if data else {}


def seed(config):
    login = wait_for('free5GC WebUI API', lambda: webui('login', {'Username': 'admin', 'Password': 'free5gc'}), 120)
    sub = config['lab']['subscriber']
    path = f"subscriber/{sub['supi']}/{sub['mcc']}{sub['mnc']}"
    data = json.loads((GENERATED / 'subscriber.json').read_text())
    token = login['access_token']
    try:
        existing = webui(path, token=token)
        method = 'PUT' if existing.get('AuthenticationSubscription', {}).get('permanentKey') else 'POST'
        if method == 'PUT':
            sequence = existing['AuthenticationSubscription'].get('sequenceNumber')
            if sequence is not None:
                data['AuthenticationSubscription']['sequenceNumber'] = sequence
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        method = 'POST'
    webui(path, data, token, method)
    print('Lab subscriber fixture reconciled (credentials not logged).', flush=True)


def database_selection(**kwargs):
    from compact_database import get
    return get(STATE, **kwargs)


def up(config, in_transaction=False, database_transaction=False):
    from compact_develop import overrides, recover
    if not in_transaction:
        recover()
    if not (STATE / 'candidate.json').exists():
        raise ValueError('no local image candidate; run explicit build first')
    candidate = json.loads((STATE / 'candidate.json').read_text())
    if candidate.get('dashboard_snapshot_protocol') != 1:
        raise ValueError('local candidate predates snapshot dashboard; run up --build once')
    selected = candidate_selection(candidate, overrides())
    for image in set(selected.values()) | {candidate['image_id']}:
        output(['docker', 'image', 'inspect', image])
    database = database_selection(allow_pending=database_transaction)
    render(config, checkout('free5gc_compose'), GENERATED, candidate['image_id'], selected, database)
    compose('config', '--quiet')
    compose('pull', '--policy', 'missing', *CORE_SERVICES)
    # A deterministic reconnect avoids retaining PFCP state from replaced core
    # containers or a new observer generation. Database data is retained.
    compose('stop', 'ue', 'ran', 'observer')
    compose('stop', *[name for name in CORE_SERVICES if name != 'db'])
    compose('up', '-d', *CORE_SERVICES, 'mupc', 'tpe', 'npe', 'dn')
    bootstrap_pes()
    wait_for('MUP-C and two BGP sessions', lambda: controller()['bgp_state'] == '2/2 established')
    compose('up', '-d', 'observer')
    wait_for('passive observer lease', lambda: controller()['observer_lease_valid'])
    seed(config)
    compose('up', '-d', 'ran')
    wait_for('gNB NG setup', lambda: 'NG Setup procedure is successful' in compose('logs', '--no-color', 'ran', capture=True))
    compose('up', '-d', 'ue')
    wait_session()
    wait_for('UE tunnel', lambda: execute('ue', 'ip', '-j', 'address', 'show', 'uesimtun0'))
    traffic(config)
    dashboard_install()
    print('Compact lab is up: real UE registration and user traffic passed.', flush=True)


def bootstrap_pes():
    for pe in ('tpe', 'npe'):
        wait_for(pe + ' Vinbero API', lambda pe=pe: execute(pe, 'vinbero', '--json', 'stats', 'show'))
        execute(pe, 'bash', f'/run/lab/{pe}-bootstrap.sh', capture=False)


def dashboard_install():
    from compact_collector import UNIT
    directory = STATE / 'dashboard'
    sockets = STATE / 'dashboard-socket'
    for path in (directory, sockets):
        if path.is_symlink():
            raise ValueError('refusing symlink dashboard directory')
        path.mkdir(exist_ok=True, mode=0o755)
        path.chmod(0o755)
    os.chown(sockets, 65534, 65534)
    probe_lock = STATE / 'probe.lock'
    if not probe_lock.exists():
        write(probe_lock, '')
    unit = f'''[Unit]
Description=Compact lab read-only state collector
After=docker.service
Requires=docker.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 {ROOT}/scripts/compact_collector.py
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={directory} {probe_lock}
[Install]
WantedBy=multi-user.target
'''
    write(Path('/etc/systemd/system') / UNIT, unit, 0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', UNIT])
    run(['systemctl', 'restart', UNIT])
    compose('up', '-d', 'dashboard')
    wait_for('fresh read-only dashboard snapshot', lambda: execute('dashboard', 'curl', '-fsS',
        '-w', '%{http_code}', '--unix-socket', '/run/socket/http.sock', 'http://localhost/healthz') == '204')


def wait_session():
    def ready():
        data = controller('sessions')
        selected = [item for item in data.get('sessions', []) if item.get('advertised')]
        return selected[0] if selected else None
    return wait_for('PFCP-derived advertised UE session', ready, 90)


def redirect_count(pe):
    stats = json.loads(execute(pe, 'vinbero', '--json', 'stats', 'show'))
    return next((item.get('packets', 0) for item in stats if item['name'] == 'REDIRECT'), 0)


def traffic(config, mup=True):
    # Exclude periodic active probes from exact packet/counter assertions, while
    # leaving read-only dashboard collection running throughout each test.
    with (STATE / 'probe.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _traffic_evidence(config, mup)


def runtime_images():
    identifiers = compose('ps', '-q', capture=True).splitlines()
    if not identifiers:
        raise ValueError('no running compact containers for evidence')
    result = {}
    for item in json.loads(output(['docker', 'inspect', *identifiers])):
        labels = item['Config'].get('Labels', {})
        service = labels.get('com.docker.compose.service')
        if labels.get('com.docker.compose.project') != 'srv6-mup-compact' or \
                service not in CORE_SERVICES + ROLE_SERVICES + EXTRA_SERVICES:
            raise ValueError('unexpected container in runtime evidence')
        result[service] = {'image_id': item['Image'], 'container_id': item['Id']}
    return result


def _traffic_evidence(config, mup=True):
    directory = STATE / 'evidence' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + ('-mup' if mup else '-baseline'))
    sessions = controller('sessions')['sessions']
    if len(sessions) != 1:
        raise ValueError('packet gate currently requires exactly one observed UE')
    session = sessions[0]['session']
    with Capture(directory):
        write(directory / 'runtime.json', json.dumps({
            'running_kernel': os.uname().release,
            'gtp5g_vermagic': output(['modinfo', '-F', 'vermagic', 'gtp5g']),
            'candidate': json.loads((STATE / 'candidate.json').read_text()),
            'deployed_images': runtime_images(),
            'verification_source_sha256': {name: digest(ROOT / name) for name in (
                'scripts/compact_runtime.py', 'scripts/compact_evidence.py',
                'scripts/compact_compose.py', 'config/versions.lock.yml')},
        }, indent=2))
        try:
            _traffic(config, mup)
        finally:
            write(directory / 'controller.json', json.dumps(controller('sessions'), indent=2))
            print(f'Private packet evidence: {directory}', flush=True)
    state = bpf_state(execute)
    write(directory / 'bpf.json', json.dumps(state, indent=2))
    map_ids = [{item['id'] for item in state[pe]['maps']} for pe in ('tpe', 'npe')]
    if map_ids[0] & map_ids[1]:
        raise ValueError('PEs unexpectedly share BPF maps')
    uplink = [item for item in state['tpe']['maps'] if item['name'] == 'mup_uplink_v4_m']
    if len(uplink) != 1 or bool(uplink[0]['entries']) != mup:
        raise ValueError('MUP PE (N3/Interwork side) instance-specific F-TEID map differs from expected path state')
    proof = verify_packets(directory, session, config['lab']['networks']['n6']['dn_ipv4'], mup)
    write(directory / 'result.json', json.dumps(proof, indent=2))
    print('PASS: correlated N3/SRv6/N6/UPF packet and PE-specific BPF evidence', flush=True)


def _traffic(config, mup=True):
    address = config['lab']['networks']['n6']['dn_ipv4']
    before = {pe: redirect_count(pe) for pe in ('tpe', 'npe')}
    execute('ue', 'ping', '-I', 'uesimtun0', '-c', '8', '-i', '0.5', '-W', '2', address, capture=False)
    body = execute('ue', 'curl', '-fsS', '--max-time', '10', '--interface', 'uesimtun0', f'http://{address}/')
    if 'free5GC SRv6 MUP lab data network' not in body:
        raise ValueError('unexpected DN HTTP response')
    after = {pe: redirect_count(pe) for pe in before}
    if mup and any(after[pe] <= before[pe] for pe in before):
        raise ValueError(f'XDP counters did not increase on both PEs: {before} -> {after}')
    if not mup and before != after:
        raise ValueError('MUP redirects changed during the ordinary UPF baseline')
    print(f'PASS: UE ICMP/HTTP; XDP REDIRECT {before} -> {after}', flush=True)


def one_call(config):
    directory = STATE / 'evidence' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-one-call')
    with Capture(directory, control=True):
        _one_call(config)
    write(directory / 'result.json', json.dumps(verify_control(directory), indent=2))
    print(f'PASS: N2/N4/BGP 1call packet evidence: {directory}', flush=True)


def _one_call(config):
    print('Explicit 1call: temporarily deregistering the lab UE.', flush=True)
    try:
        execute('ue', 'nr-cli', config['lab']['subscriber']['supi'], '--exec', 'deregister switch-off')
        compose('stop', 'ue')
        wait_for('PFCP deletion and T1/T2 withdrawal', lambda: controller()['observed_sessions'] == 0 and controller()['advertised_routes'] == 0)
    finally:
        since = datetime.now(timezone.utc).isoformat()
        compose('up', '-d', 'ue')
    for message in ('Initial Registration is successful', 'PDU Session establishment is successful'):
        wait_for(message, lambda message=message: message in compose('logs', '--since', since, '--no-color', 'ue', capture=True))
    wait_session()
    wait_for('UE tunnel', lambda: execute('ue', 'ip', '-j', 'address', 'show', 'uesimtun0'))
    traffic(config)
    print('PASS: one-call Registration -> PDU -> PFCP -> MUP -> ICMP/HTTP', flush=True)


def baseline(config):
    session = wait_session()
    key = session['session']['key']
    controller('suppress', key)
    try:
        wait_for('MUP withdrawal', lambda: controller()['advertised_routes'] == 0)
        traffic(config, mup=False)
    finally:
        controller('resume', key)
    wait_session()
    traffic(config)
    print('PASS: ordinary UPF fallback and MUP resume for the same PFCP session', flush=True)


def lease(config):
    wait_session()
    compose('stop', 'observer')
    try:
        wait_for('observer lease expires and routes withdraw', lambda:
                 not controller()['observer_lease_valid'] and controller()['advertised_routes'] == 0)
        traffic(config, mup=False)
    finally:
        compose('up', '-d', 'observer')
    wait_for('new observer lease', lambda: controller()['observer_lease_valid'])
    # Passive snooping cannot reconstruct sessions that predate its process.
    # Reconnect the real UE; never inject sessions/TEIDs to mask that limitation.
    one_call(config)
    print('PASS: lease expiry fallback and real UE reconnect recovery', flush=True)


def guest_guard(config):
    if os.geteuid() != 0:
        raise ValueError('guest runtime requires root inside the dedicated VM')
    if Path('/etc/hostname').read_text().strip() != config['vm']['name']:
        raise ValueError('runtime hostname guard: this is not the configured compact VM')
    if output(['systemd-detect-virt']) != 'kvm':
        raise ValueError('compact runtime requires its isolated KVM guest')


def fetch(url, path, expected):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        partial = path.with_suffix('.partial')
        run(['curl', '-fL', '--retry', '3', '-o', partial, url])
        if digest(partial) != expected:
            raise ValueError(f'checksum mismatch: {path.name}')
        partial.rename(path)
    if digest(path) != expected:
        raise ValueError(f'cached checksum mismatch: {path.name}')


def checkout(name):
    spec = (locks()['compact_nf_sources'][name.removeprefix('free5gc-')]
            if name.startswith('free5gc-') else locks()['sources'][name])
    path = SOURCES / name
    if not path.exists():
        path.mkdir(parents=True)
        run(['git', 'init', '-q', path])
        run(['git', '-C', path, 'remote', 'add', 'origin', spec['repository']])
        run(['git', '-C', path, 'fetch', '--depth=1', 'origin', spec['commit']])
        run(['git', '-C', path, 'checkout', '--detach', 'FETCH_HEAD'])
    if output(['git', '-C', path, 'rev-parse', 'HEAD']) != spec['commit']:
        raise ValueError(f'{name} checkout differs from lock; refusing to overwrite source')
    return path


def prepare(config):
    STATE.mkdir(parents=True, exist_ok=True)
    kernel = os.uname().release
    if not kernel.startswith('6.8.'):
        raise ValueError(f'M1 requires the planned 6.8 kernel candidate, found {kernel}')
    marker = STATE / 'prepared.json'
    desired = {'build_schema': 3, 'kernel': kernel, 'gtp5g': locks()['sources']['gtp5g']['commit'],
               'compose': locks()['toolchains']['compose']}
    if marker.exists() and json.loads(marker.read_text()) == desired and module_matches(kernel):
        for module in ('gtp5g', 'vrf', 'sctp', 'tun'):
            run(['modprobe', module])
        return
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    run(['apt-get', 'update'], env=env)
    run(['apt-get', 'install', '-y', 'docker.io', 'git', 'curl', 'ca-certificates',
         'build-essential', 'libmnl-dev', 'libelf-dev', 'jq', 'tcpdump',
         'iproute2', 'iputils-ping', 'ethtool', f'linux-headers-{kernel}',
         f'linux-modules-extra-{kernel}', f'linux-tools-{kernel}'], env=env)
    run(['systemctl', 'enable', '--now', 'docker'])
    compose_spec = locks()['toolchains']['compose']
    plugin = Path('/usr/local/lib/docker/cli-plugins/docker-compose')
    fetch(compose_spec['url'], plugin, compose_spec['sha256'])
    plugin.chmod(0o755)
    source = checkout('gtp5g')
    # Kbuild can retain a previous kernel's .ko across a same-series update.
    # A successful modprobe with CONFIG_MODVERSIONS is not proof of a rebuild.
    run(['make', 'clean'], cwd=source)
    run(['make', '-j2'], cwd=source)
    if output(['modinfo', '-F', 'vermagic', source / 'gtp5g.ko']).split()[0] != kernel:
        raise ValueError('rebuilt gtp5g vermagic does not match the running guest kernel')
    if Path('/sys/module/gtp5g').exists():
        if (GENERATED / 'compose.yml').exists():
            compose('stop', 'ue', 'ran', 'observer', *[name for name in CORE_SERVICES if name != 'db'])
        run(['modprobe', '-r', 'gtp5g'])
    run(['make', 'install'], cwd=source)
    for module in ('gtp5g', 'vrf', 'sctp', 'tun'):
        run(['modprobe', module])
    write('/etc/modules-load.d/srv6-mup-compact.conf', 'udp_tunnel\ngtp5g\nvrf\nsctp\ntun\n', 0o644)
    write(marker, json.dumps(desired, indent=2))
    print(f'M1 kernel gate: gtp5g built and loaded on {kernel}', flush=True)


def prepare_build_tools():
    """Add application-specific tools only for an explicit full local build."""
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    run(['apt-get', 'update'], env=env)
    run(['apt-get', 'install', '-y', 'cmake', 'libsctp-dev', 'lksctp-tools',
         'clang', 'llvm', 'gcc-multilib', 'libbpf-dev'], env=env)
    go = locks()['toolchains']['go']
    archive = STATE / f"go{go['version']}.tar.gz"
    fetch(go['url'], archive, go['sha256'])
    toolchain = Path('/opt') / ('go-' + go['version'])
    if not (toolchain / 'bin/go').exists():
        toolchain.mkdir(exist_ok=True)
        run(['tar', '-xzf', archive, '--strip-components=1', '-C', toolchain])


def module_matches(kernel):
    try:
        return output(['modinfo', '-F', 'vermagic', 'gtp5g']).split()[0] == kernel
    except (subprocess.CalledProcessError, IndexError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'build', 'check-release', 'install-release', 'up', 'status', 'health', 'evidence', 'logs', 'test', 'diagnose', 'rebuild', 'rollback', 'builds', 'database'])
    parser.add_argument('argument', nargs='?')
    parser.add_argument('build_id', nargs='?')
    parser.add_argument('--clean-runtime', action='store_true')
    parser.add_argument('--local-builder', action='store_true')
    args = parser.parse_args()
    config = load()
    guest_guard(config)
    STATE.mkdir(parents=True, exist_ok=True)
    lock = (STATE / 'operation.lock').open('a')
    if args.command in ('prepare', 'build', 'check-release', 'install-release', 'up', 'test', 'health', 'rebuild', 'rollback', 'database'):
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another guest lifecycle/test operation is running') from None
    if args.command in ('prepare', 'build', 'install-release', 'up', 'test', 'rebuild', 'rollback'):
        from compact_database import idle
        idle(STATE)
    if args.command == 'prepare':
        prepare(config)
    elif args.command == 'build':
        build(config)
    elif args.command in ('check-release', 'install-release'):
        from compact_release import read, install, selection_guard
        data = read(STATE / 'release-request.json', require_source=True)
        if args.command == 'check-release':
            selection_guard(data)
        else:
            install(config, data)
    elif args.command == 'up':
        up(config)
    elif args.command == 'rebuild':
        from compact_develop import rebuild
        rebuild(config, args.argument, args.build_id, args.clean_runtime, args.local_builder)
    elif args.command == 'rollback':
        from compact_develop import rollback
        rollback(config, args.argument)
    elif args.command == 'builds':
        from compact_develop import builds
        builds()
    elif args.command == 'database':
        from compact_database import upgrade, recover
        if args.argument == 'status':
            print(json.dumps(database_selection(create=False), indent=2))
        elif args.argument == 'upgrade':
            upgrade(config, STATE)
        elif args.argument == 'recover':
            recover(config, STATE)
        else:
            raise ValueError('unknown database operation')
    elif args.command == 'status':
        compose('ps', '--all')
        print(json.dumps(controller(), indent=2))
    elif args.command == 'diagnose':
        from compact_diagnostics import guest
        guest()
    elif args.command == 'health':
        status = controller()
        if not status['observer_lease_valid'] or status['bgp_state'] != '2/2 established' or status['advertised_routes'] != 2:
            raise ValueError('compact control plane is not ready')
        for pe in ('tpe', 'npe'):
            execute(pe, 'vinbero', '--json', 'stats', 'show')
        execute('ue', 'ping', '-I', 'uesimtun0', '-c', '1', '-W', '2', config['lab']['networks']['n6']['dn_ipv4'])
        execute('dashboard', 'curl', '-fsS', '--unix-socket', '/run/socket/http.sock', 'http://localhost/healthz')
        print('PASS: observer lease, BGP 2/2, T1/T2, both PE APIs, UE ping and fresh dashboard')
    elif args.command == 'evidence':
        for path in sorted((STATE / 'evidence').glob('*/result.json')):
            print(json.dumps({'directory': str(path.parent), 'result': json.loads(path.read_text())}))
    elif args.command == 'logs':
        if args.argument not in CORE_SERVICES + ROLE_SERVICES + EXTRA_SERVICES:
            raise ValueError('unknown service')
        compose('logs', '--tail', '100', '--no-color', args.argument)
    elif args.command == 'test':
        if args.argument == 'one-call':
            one_call(config)
        elif args.argument in ('baseline', 'mup'):
            baseline(config)
        elif args.argument == 'lease':
            lease(config)
        elif args.argument in ('restart', 'network', 'neighbor'):
            from compact_recovery import run as recovery_test
            recovery_test(config, args.argument)
        elif args.argument == 'all':
            one_call(config)
            baseline(config)
            lease(config)
            from compact_recovery import SCENARIOS, run as recovery_test
            for scenario in SCENARIOS:
                recovery_test(config, scenario)
            print('PASS: compact suite (one-call, baseline/MUP, lease, restart, network, neighbor); clean/second-host reproduction is separate', flush=True)
        else:
            raise ValueError('unknown test')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'ERROR: {error}')
