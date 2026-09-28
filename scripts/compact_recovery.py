"""Explicit, disruptive recovery gates confined to the owned compact guest."""
from datetime import datetime, timezone
import fcntl
import json
import subprocess

import compact_runtime as runtime
from compact_vm import digest, output, write

SCENARIOS = ('restart', 'network', 'neighbor')


def network_state(config):
    """Assert the actual route/VRF state, not just generated configuration."""
    nets = config['lab']['networks']
    result = {}
    for pe, other in (('tpe', 'npe'), ('npe', 'tpe')):
        routes = json.loads(runtime.execute(pe, 'ip', '-j', '-6', 'route', 'show',
                                           nets['sr_underlay'][other + '_locator']))
        if not any(item.get('gateway') == nets['sr_underlay'][other + '_ipv6']
                   and item.get('dev') == 'sr' for item in routes):
            raise ValueError(f'{pe}: missing peer locator route on sr')
        result[pe + '_locator_route'] = routes
    link = json.loads(runtime.execute('npe', 'ip', '-j', 'link', 'show', 'n6'))
    if len(link) != 1 or link[0].get('master') != 'mup-dn':
        raise ValueError('npe: N6 is not enslaved to mup-dn')
    vrf = json.loads(runtime.execute('npe', 'ip', '-j', '-d', 'link', 'show', 'mup-dn'))
    if len(vrf) != 1 or vrf[0].get('linkinfo', {}).get('info_kind') != 'vrf' or \
            vrf[0]['linkinfo'].get('info_data', {}).get('table') != 100:
        raise ValueError('npe: mup-dn is not VRF table 100')
    for name, prefix in (('n6', nets['n6']['subnet']), ('ue', nets['ue']['pool'])):
        routes = json.loads(runtime.execute('npe', 'ip', '-j', 'route', 'show', 'table', '100', prefix))
        if not any(item.get('dev') == 'n6' and
                   (item.get('scope') == 'link' if name == 'n6' else
                    item.get('gateway') == nets['n6']['upf_ipv4']) for item in routes):
            raise ValueError(f'npe: missing {name} route in VRF table 100')
        result[name + '_vrf_route'] = routes
    result.update(n6_link=link, vrf=vrf)
    return result


def pe_state():
    result = {}
    for pe in ('tpe', 'npe'):
        identifier = runtime.compose('ps', '-q', pe, capture=True)
        if not identifier:
            raise ValueError(f'{pe}: no running container')
        item = json.loads(output(['docker', 'inspect', identifier]))[0]
        labels = item['Config'].get('Labels', {})
        if labels.get('com.docker.compose.project') != 'srv6-mup-compact' or \
                labels.get('com.docker.compose.service') != pe or not item['State']['Running']:
            raise ValueError('PE container ownership/running-state mismatch')
        result[pe] = {'id': item['Id'], 'image': item['Image'], 'started': item['State']['StartedAt'],
                      'namespace': item['NetworkSettings']['SandboxID']}
    return result


def changed(before, after, recreate):
    for pe in ('tpe', 'npe'):
        old, new = before[pe], after[pe]
        if old['image'] != new['image'] or old['started'] == new['started']:
            raise ValueError(f'{pe}: restart did not occur with the same image')
        if recreate:
            if old['id'] == new['id'] or not new['namespace'] or old['namespace'] == new['namespace']:
                raise ValueError(f'{pe}: container/network namespace was not recreated')
        elif old['id'] != new['id']:
            raise ValueError(f'{pe}: restart unexpectedly replaced the container')


def ready():
    # Same managed bootstrap used by `up`; not a claim of unattended/hitless
    # recovery from arbitrary daemon crashes. Never recreate a UE/PFCP session.
    runtime.bootstrap_pes()
    runtime.wait_for('recovered BGP sessions', lambda: runtime.controller()['bgp_state'] == '2/2 established')


def pe_recovery(config, recreate):
    session = runtime.wait_session()['session']['key']
    before = pe_state()
    try:
        if recreate:
            runtime.compose('up', '-d', '--no-deps', '--force-recreate', 'tpe', 'npe')
        else:
            runtime.compose('restart', 'tpe', 'npe')
        ready()
        after = pe_state()
        changed(before, after, recreate)
        network = network_state(config)
        if runtime.wait_session()['session']['key'] != session:
            raise ValueError('PE recovery unexpectedly replaced the PFCP session')
        # Prove the preexisting session recovers before a later fresh 1call.
        runtime.traffic(config)
        runtime.baseline(config)
        runtime.one_call(config)
    except (ValueError, OSError, subprocess.SubprocessError):
        try:
            runtime.compose('up', '-d', '--no-deps', 'tpe', 'npe')
            ready()
        except (ValueError, OSError, subprocess.SubprocessError) as recovery_error:
            print(f'WARNING: PE recovery also failed: {recovery_error}', flush=True)
        raise
    return {'before': before, 'after': after, 'network': network,
            'existing_session_recovered': True, 'baseline_and_fresh_one_call': True}


def neighbor_state(address):
    return json.loads(runtime.execute('npe', 'ip', '-j', 'neigh', 'show', address, 'dev', 'n6'))


def usable_neighbor(items):
    return any(item.get('lladdr') and set(item.get('state', [])) &
               {'REACHABLE', 'STALE', 'DELAY', 'PROBE'} for item in items)


def ready_neighbor(address):
    items = neighbor_state(address)
    return items if usable_neighbor(items) else None


def neighbor_recovery(config):
    address = config['lab']['networks']['n6']['dn_ipv4']
    network = network_state(config)
    # Only the installed MUP PE (N6/Direct side) periodic refresh may send probes while we wait.
    # In particular, collector UE ping must not repair the cache for this test.
    with (runtime.STATE / 'probe.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        before = runtime.wait_for('dynamic DN neighbor before eviction',
                                  lambda: ready_neighbor(address), 30)
        if any(set(item.get('state', [])) & {'PERMANENT', 'NOARP'} for item in before):
            raise ValueError('refusing to evict a static DN neighbor')
        runtime.execute('npe', 'ip', 'neigh', 'del', address, 'dev', 'n6')
        removed = neighbor_state(address)
        if usable_neighbor(removed):
            raise ValueError('DN neighbor was not observed absent after eviction; retry the test')
        after = runtime.wait_for('automatic DN neighbor recovery without UE/manual probes',
                                 lambda: ready_neighbor(address), 30)
    runtime.traffic(config)
    runtime.one_call(config)
    return {'network': network, 'before': before, 'after_eviction': removed, 'after': after,
            'collector_probe_paused': True, 'manual_refresh_used': False}


def run(config, scenario):
    if scenario not in SCENARIOS:
        raise ValueError('unknown recovery scenario')
    directory = runtime.STATE / 'evidence' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + scenario)
    directory.mkdir(parents=True, mode=0o700)
    previous_evidence = set(directory.parent.glob('*/result.json'))
    result = {'scenario': scenario, 'passed': False, 'started': datetime.now(timezone.utc).isoformat(),
              'source_sha256': digest(__file__)}
    write(directory / 'attempt.json', json.dumps(result, indent=2))
    try:
        result['network_before'] = network_state(config)
        result['checks'] = (neighbor_recovery(config) if scenario == 'neighbor' else
                            pe_recovery(config, recreate=scenario == 'network'))
        result['passed'] = True
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result['error'] = str(error)
        raise
    finally:
        result['packet_evidence'] = [str(path.parent) for path in
                                    sorted(set(directory.parent.glob('*/result.json')) - previous_evidence)]
        result['finished'] = datetime.now(timezone.utc).isoformat()
        write(directory / 'result.json', json.dumps(result, indent=2))
        print(f'Private recovery evidence: {directory}', flush=True)
    print(f'PASS: compact {scenario} recovery, routes/VRF and real packet gates', flush=True)
