"""Read-only bounded diagnostics; never includes environment, config or raw logs."""
import json
import os
import subprocess
import urllib.request


def check(results, name, action):
    try:
        value = action()
        results[name] = {'ok': True, 'value': value}
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        results[name] = {'ok': False, 'error': type(error).__name__}


def command(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=8).strip()


def host(vm):
    results = {}
    check(results, 'ownership', lambda: {'domain_uuid': vm.owned()['domain_uuid']})
    check(results, 'preflight', vm.preflight)
    check(results, 'vm', lambda: command(['virsh', '-c', 'qemu:///system', 'domstate', vm.vm['name']]))
    check(results, 'dashboard_service', lambda: command(['systemctl', '--user', 'show',
          vm.vm['name'] + '-dashboard.service', '--property=ActiveState,SubState']))
    def snapshot():
        with urllib.request.urlopen(f"http://127.0.0.1:{vm.config['dashboard_port']}/api/state", timeout=3) as response:
            state = json.load(response)
        return {key: state.get(key) for key in ('updated_at', 'stale', 'overall', 'paths', 'uplane_probe', 'errors')}
    check(results, 'dashboard_snapshot', snapshot)
    print(json.dumps({'host': results}, indent=2), flush=True)
    if results['ownership']['ok'] and results['vm'].get('value') == 'running':
        vm.runtime('diagnose')


def guest():
    from compact_runtime import STATE
    from compact_collector import collect
    from compact_config import load
    results = {'kernel': {'ok': True, 'value': os.uname().release}}
    check(results, 'gtp5g_vermagic', lambda: command(['modinfo', '-F', 'vermagic', 'gtp5g']))
    check(results, 'docker', lambda: command(['docker', 'version', '--format', '{{.Server.Version}}']))
    check(results, 'compose', lambda: command(['docker', 'compose', 'version', '--short']))
    check(results, 'collector', lambda: command(['systemctl', 'is-active', 'srv6-mup-compact-collector.service']))
    def candidate():
        manifest = json.loads((STATE / 'candidate.json').read_text())
        return {key: manifest.get(key) for key in ('image', 'image_id', 'kernel', 'dashboard_snapshot_protocol')}
    check(results, 'candidate', candidate)
    # Skip the active ping: diagnose is read-only even during a packet gate.
    check(results, 'runtime', lambda: collect(load(), active_probe=False))
    print(json.dumps({'guest': results}, indent=2))
