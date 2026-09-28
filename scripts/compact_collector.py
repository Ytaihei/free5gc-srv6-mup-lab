"""Guest-local collector. Writes data only; exposes no command/API listener."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

from compact_config import load, ROOT
from compact_compose import CORE_SERVICES, ROLE_SERVICES

STATE = ROOT / '.lab/runtime'
UNIT = 'srv6-mup-compact-collector.service'
# These are display labels, not replacements for stable Compose/API role IDs.
PE_LABELS = {'tpe': 'MUP PE (N3/Interwork side)', 'npe': 'MUP PE (N6/Direct side)'}


def timestamp(value=None):
    return datetime.fromtimestamp(value or time.time(), timezone.utc).isoformat()


def query(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=4).strip()


def execute(service, *args):
    if service not in CORE_SERVICES + ROLE_SERVICES:
        raise ValueError('unknown collector service')
    return query(['docker', 'exec', 'srv6-mup-compact-' + service + '-1', *args])


def probe(target):
    # A shared lock coordinates ONLY the active ping with packet evidence tests.
    # Read-only collection continues so registration/withdrawal remains visible.
    with (STATE / 'probe.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'idle', 'success': False, 'target': target,
                    'checked_at': timestamp(), 'interval_seconds': 5,
                    'error': 'active probe paused during packet evidence test'}
        result = {'status': 'failed', 'success': False, 'target': target,
                  'checked_at': timestamp(), 'interval_seconds': 5}
        try:
            value = execute('ue', 'ping', '-n', '-I', 'uesimtun0', '-c', '1', '-W', '1', target)
            source = re.search(r'from ([0-9.]+)', value)
            rtt = re.search(r'rtt .* = [0-9.]+/([0-9.]+)/', value)
            if not source or not rtt:
                raise ValueError('invalid ping response')
            result.update(status='ok', success=True, source=source[1], rtt_ms=float(rtt[1]))
        except (ValueError, OSError, subprocess.SubprocessError):
            result['error'] = 'ICMP timeout or UE tunnel unavailable'
        return result


def collect(config, active_probe=True):
    start = time.time()
    server = f"http://{config['lab']['nodes']['mupc']['management_ipv4']}:9443"
    jobs = {
        'containers': lambda: query(['docker', 'ps', '-a', '--filter',
            'label=com.docker.compose.project=srv6-mup-compact', '--format', '{{json .}}']),
        'controller': lambda: json.loads(execute('mupc', 'mupctl', '--server', server, 'status')),
        'sessions': lambda: json.loads(execute('mupc', 'mupctl', '--server', server, 'sessions')),
        'probe': lambda: probe(config['lab']['networks']['n6']['dn_ipv4']) if active_probe else
            {'status': 'idle', 'success': False, 'error': 'read-only diagnostics: no active probe'},
    }
    for role in ('tpe', 'npe'):
        for name, command in [('stats', ['stats', 'show']), ('routes', ['mup', 'list']), ('headends', ['headend-v4', 'list'])]:
            jobs[role + '-' + name] = lambda role=role, command=command: json.loads(execute(role, 'vinbero', '--json', *command))
    values, errors = {}, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, future in pending.items():
            try:
                values[name] = future.result()
            except (ValueError, OSError, subprocess.SubprocessError):
                errors.append(name + ': collection failed')
    state = {'updated_at': timestamp(start), 'collection_duration_ms': round((time.time()-start)*1000),
             'overall': 'degraded', 'controller': {}, 'sessions': [], 'nodes': [], 'pes': [],
             'paths': {'mup_active': False, 'fallback_active': False}, 'errors': errors,
             'uplane_probe': values.get('probe', {'status': 'failed', 'success': False})}
    controller = values.get('controller', {})
    if controller:
        state['controller'] = {key: controller.get(key) for key in
            ('observer_id', 'observer_lease_valid', 'observed_sessions', 'selected_sessions', 'advertised_routes', 'bgp_state')}
        seen = int(controller.get('observer_last_seen_unix_nano', 0))/1e9
        state['controller'].update(available=True, generation=int(controller.get('generation', 0)),
            observer_last_seen=timestamp(seen) if seen else '0001-01-01T00:00:00Z',
            observer_age_seconds=max(0, start-seen))
    for item in values.get('sessions', {}).get('sessions', []):
        session = dict(item['session'])
        observed = int(session.pop('observed_unix_nano', 0))/1e9
        session['observed_at'] = timestamp(observed) if observed else '0001-01-01T00:00:00Z'
        for key in ('cp_seid', 'up_seid'):
            session[key] = int(session.get(key, 0))
        session.update({key: item.get(key, False if key != 'reason' else '') for key in
                        ('selected', 'suppressed', 'reason', 'advertised')})
        state['sessions'].append(session)
    containers = {}
    for line in values.get('containers', '').splitlines():
        item = json.loads(line)
        containers[item['Names']] = item
    groups = {'core': CORE_SERVICES + ['observer'], 'ran': ['ran', 'ue'],
              'tpe': ['tpe'], 'npe': ['npe'], 'mupc': ['mupc'], 'dn': ['dn']}
    for role, names in groups.items():
        services = []
        for name in names:
            item = containers.get('srv6-mup-compact-' + name + '-1', {})
            active = item.get('State') == 'running' and '(unhealthy)' not in item.get('Status', '')
            services.append({'name': name, 'active': active, 'state': item.get('Status', 'missing')})
        state['nodes'].append({'id': role, 'name': role, 'role': PE_LABELS.get(role, role),
            'address': config['lab']['nodes'][role]['management_ipv4'],
            'reachable': 'containers' in values, 'healthy': all(s['active'] for s in services), 'services': services})
    for role in ('tpe', 'npe'):
        state['pes'].append({'id': role, 'name': PE_LABELS[role], **{name: values.get(role + '-' + name, []) for name in ('stats', 'routes', 'headends')}})
    active = bool(controller.get('observer_lease_valid') and controller.get('advertised_routes', 0) >= 2
                  and any(s['advertised'] for s in state['sessions']))
    state['paths'] = {'mup_active': active, 'fallback_active': bool(controller) and not active}
    if not controller:
        state['overall'] = 'offline'
    elif (not errors and all(n['healthy'] for n in state['nodes'])
          and controller.get('observer_lease_valid') and controller.get('bgp_state') == '2/2 established'
          and state['uplane_probe'].get('success')):
        state['overall'] = 'healthy'
    return state


def main():
    from compact_runtime import guest_guard
    config = load()
    guest_guard(config)
    directory = STATE / 'dashboard'
    while True:
        started = time.monotonic()
        snapshot = collect(config)
        temporary = directory / 'state.tmp'
        temporary.write_text(json.dumps(snapshot))
        temporary.chmod(0o644)
        os.replace(temporary, directory / 'state.json')
        time.sleep(max(0.1, 5-(time.monotonic()-started)))


if __name__ == '__main__':
    main()
