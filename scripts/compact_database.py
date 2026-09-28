"""Owned compact MongoDB selection and explicit, cold-clone major upgrades.

Never attach a newer major to the retained source volume. A failed operation
leaves its journal and both volumes; normal lifecycle commands fail closed.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from compact_config import ROOT, locks
from compact_vm import output, run

PROJECT = 'srv6-mup-compact'
LEGACY = PROJECT + '_dbdata'
LABEL = 'io.srv6-mup.database'


def atomic(path, value):
    from compact_develop import atomic as save
    save(path, value)


def validate(value):
    spec = locks()['compact']['database']
    images = {'4.4': locks()['containers']['images']['mongo'], spec['series']: spec['image']}
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'series', 'image', 'volume'}
            or value['schema_version'] != 1 or value['series'] not in images
            or value['image'] != images[value['series']]
            or not isinstance(value['volume'], str)
            or not (value['volume'] == LEGACY and value['series'] == '4.4'
                    or re.fullmatch(PROJECT + r'_db8_[a-f0-9]{32}', value['volume']))):
        raise ValueError('invalid compact database image/volume selection')
    return dict(value)


def idle(state):
    if (state / 'database/pending.json').exists():
        raise ValueError('unfinished database migration; use database recover, not up/rebuild')


def inspect_volume(name):
    rows = json.loads(output(['docker', 'volume', 'inspect', name]))
    if len(rows) != 1 or rows[0]['Name'] != name or rows[0].get('Driver') != 'local' or rows[0].get('Options'):
        raise ValueError('unexpected database volume identity/driver/options')
    labels = rows[0].get('Labels') or {}
    if name == LEGACY:
        if labels.get('com.docker.compose.project') != PROJECT or labels.get('com.docker.compose.volume') != 'dbdata':
            raise ValueError('unowned legacy database volume')
    elif labels.get(LABEL) != '1':
        raise ValueError('unowned migration database volume')
    return rows[0]


def get(state, *, create=True, allow_pending=False):
    if not allow_pending:
        idle(state)
    path = state / 'database/selection.json'
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('refusing symlink database state')
    if path.exists():
        value = validate(json.loads(path.read_text()))
        inspect_volume(value['volume'])
        return value
    names = output(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
    if (any(n.startswith(PROJECT + '_db8_') for n in names)
            or any((state / 'database').glob('*/result.json'))):
        raise ValueError('missing database selection with migration evidence; restore reviewed state, never guess legacy or fresh')
    if LEGACY in names:
        ids = output(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT,
                      '--filter', 'label=com.docker.compose.service=db']).split()
        for item in json.loads(output(['docker', 'inspect', *ids])) if ids else []:
            mounts = [m for m in item['Mounts'] if m['Destination'] == '/data/db']
            if (item['Config']['Image'] != locks()['containers']['images']['mongo']
                    or len(mounts) != 1 or mounts[0].get('Name') != LEGACY):
                raise ValueError('missing database selection conflicts with existing container; refusing legacy fallback')
        inspect_volume(LEGACY)
        value = {'schema_version': 1, 'series': '4.4',
                 'image': locks()['containers']['images']['mongo'], 'volume': LEGACY}
    else:
        if 'avx' not in Path('/proc/cpuinfo').read_text().split():
            raise ValueError('fresh MongoDB 8.0 requires an AVX-capable guest CPU')
        containers = output(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT,
                             '--filter', 'label=com.docker.compose.service=db'])
        if containers:
            raise ValueError('database container without known volume; refusing empty replacement')
        spec = locks()['compact']['database']
        value = {'schema_version': 1, 'series': spec['series'], 'image': spec['image'],
                 'volume': PROJECT + '_db8_' + uuid.uuid4().hex}
        if create:
            create_volume(value['volume'])
    if create:
        atomic(path, value)
    return validate(value)


def create_volume(name):
    names = output(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines()
    if name in names:
        raise ValueError('database clone volume already exists; refusing reuse')
    run(['docker', 'volume', 'create', '--label', LABEL + '=1', name])
    inspect_volume(name)


def consumers(volume, *, stopped=False):
    ids = output(['docker', 'ps', '-aq', '--filter', 'volume=' + volume]).split()
    for item in json.loads(output(['docker', 'inspect', *ids])) if ids else []:
        labels = item['Config'].get('Labels') or {}
        owned = (labels.get('com.docker.compose.project') == PROJECT and
                 labels.get('com.docker.compose.service') == 'db') or labels.get(LABEL) == '1'
        if not owned or (stopped and item['State']['Running']):
            raise ValueError('database volume has unexpected or running consumers')


def client(container, script):
    image = locks()['compact']['database']['image']
    result = output(['docker', 'run', '--rm', '--network', 'container:' + container,
                     '--cap-drop=ALL', '--security-opt=no-new-privileges',
                     '--tmpfs', '/data/db', '--tmpfs', '/data/configdb',
                     '--entrypoint', 'mongosh', image, '--quiet', '--norc', '--eval', script])
    return json.loads(result)


def server_state(container):
    return client(container, 'print(JSON.stringify({version:db.version(),fcv:db.adminCommand('
                  '{getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version}))')


def fingerprint(container):
    # A fixed BSON-aware client at every stage; only hashes/counts/metadata leave
    # MongoDB, not subscriber keys or user documents. Never evaluate stored data.
    script = (ROOT / 'scripts/compact-database-fingerprint.js').read_text()
    return client(container, script)


def stop_stage(name):
    run(['docker', 'stop', '--time', '60', name], stdout=subprocess.DEVNULL)
    info = json.loads(output(['docker', 'inspect', name]))[0]
    if info['State']['Running'] or info['State']['ExitCode'] != 0:
        raise ValueError('database did not shut down cleanly; volume retained')


def stage(name, image, volume):
    consumers(volume, stopped=True)
    run(['docker', 'run', '-d', '--name', name, '--label', LABEL + '=1', '--network', 'none',
         '--mount', f'type=volume,src={volume},dst=/data/db', '--tmpfs', '/data/configdb',
         image, 'mongod', '--bind_ip', '127.0.0.1', '--setParameter', 'ttlMonitorEnabled=false'],
        stdout=subprocess.DEVNULL)
    end = time.monotonic() + 120
    while time.monotonic() < end:
        try:
            return server_state(name)
        except (ValueError, subprocess.SubprocessError):
            if not json.loads(output(['docker', 'inspect', name]))[0]['State']['Running']:
                break
            time.sleep(2)
    raise ValueError('isolated database stage did not start; inspect private container logs')


def clone(before, after):
    inspect_volume(before['volume']); consumers(before['volume'], stopped=True)
    create_volume(after['volume'])
    # Cold source is read-only. Explicit named volumes only; no host data path.
    run(['docker', 'run', '--rm', '--network', 'none', '--cap-drop=ALL',
         '--cap-add=CHOWN', '--cap-add=DAC_OVERRIDE', '--cap-add=FOWNER',
         '--security-opt=no-new-privileges',
         '--mount', f'type=volume,src={before["volume"]},dst=/source,readonly',
         '--mount', f'type=volume,src={after["volume"]},dst=/destination',
         locks()['compact']['runtime_base'], 'cp', '-a', '/source/.', '/destination/'])


def upgrade(config, state):
    from compact_runtime import compose, up
    from compact_develop import DEV
    idle(state)
    if (DEV / 'pending.json').exists():
        raise ValueError('recover component activation before database migration')
    before = get(state)
    if before['series'] == '8.0':
        print('Database already selects the locked 8.0 image; no migration performed.')
        return
    if 'avx' not in Path('/proc/cpuinfo').read_text().split():
        raise ValueError('MongoDB 5+ requires AVX in the guest CPU')
    spec = locks()['compact']['database']
    for image in set(spec['stages'].values()) | {before['image'], locks()['compact']['runtime_base']}:
        run(['docker', 'pull', image], stdout=subprocess.DEVNULL)
    container = compose('ps', '-q', 'db', capture=True)
    if not container:
        raise ValueError('start the existing database before migration')
    info = json.loads(output(['docker', 'inspect', container]))[0]
    expected = output(['docker', 'image', 'inspect', '--format', '{{.Id}}', before['image']])
    mounts = [m for m in info['Mounts'] if m['Destination'] == '/data/db']
    if info['Image'] != expected or len(mounts) != 1 or mounts[0].get('Name') != before['volume']:
        raise ValueError('running database does not match persisted selection')
    consumers(before['volume'])
    current = server_state(container)
    if not current['version'].startswith('4.4.') or current['fcv'] != '4.4':
        raise ValueError('requires standalone MongoDB 4.4 with FCV 4.4')
    identifier = uuid.uuid4().hex
    after = validate({'schema_version': 1, 'series': spec['series'], 'image': spec['image'],
                      'volume': PROJECT + '_db8_' + identifier})
    journal = {'schema_version': 1, 'id': identifier, 'before': before, 'after': after,
               'phase': 'stopping', 'stages': [], 'source_retained': True}
    pending = state / 'database/pending.json'
    atomic(pending, journal)
    # All app writers are inside this project. Do not stop the six-VM lab.
    from compact_compose import CORE_SERVICES, ROLE_SERVICES, EXTRA_SERVICES
    compose('stop', *[n for n in CORE_SERVICES + ROLE_SERVICES + EXTRA_SERVICES if n != 'db'])
    fingerprint_before = fingerprint(container)
    atomic(state / 'database' / identifier / 'before.json', fingerprint_before)
    compose('stop', '--timeout', '60', 'db')
    info = json.loads(output(['docker', 'inspect', container]))[0]
    if info['State']['Running'] or info['State']['ExitCode'] != 0:
        raise ValueError('source MongoDB did not shut down cleanly')
    clone(before, after)
    journal['phase'] = 'upgrading'; atomic(pending, journal)
    for series, image in [('4.4', before['image']), *spec['stages'].items()]:
        name = PROJECT + '-db-migrate-' + identifier + '-' + series.replace('.', '')
        actual = stage(name, image, after['volume'])
        if not actual['version'].startswith(series + '.'):
            raise ValueError('stage binary version differs from lock')
        if fingerprint(name) != fingerprint_before:
            raise ValueError('application BSON/index/options changed during migration; not activated')
        change = {'setFeatureCompatibilityVersion': series}
        if int(series.split('.')[0]) >= 7:
            change['confirm'] = True
        result = client(name, 'print(JSON.stringify(db.adminCommand(' + json.dumps(change) + ')))')
        if result.get('ok') != 1:
            raise ValueError('FCV transition failed')
        actual = server_state(name)
        if actual['fcv'] != series or fingerprint(name) != fingerprint_before:
            raise ValueError('post-FCV verification failed; retained volumes require review')
        stop_stage(name)
        journal['stages'].append({'series': series, 'image': image, **actual})
        atomic(pending, journal)
        print('PASS: isolated MongoDB ' + series + ' and application data/index preservation', flush=True)
    # From this point recovery must resume the NEW volume: app writes may occur.
    journal['phase'] = 'activating'; atomic(pending, journal)
    atomic(state / 'database/selection.json', after)
    up(config, database_transaction=True)
    finish(state, journal, 'verified')


def finish(state, journal, status):
    journal['phase'] = status
    atomic(state / 'database' / journal['id'] / 'result.json', journal)
    (state / 'database/pending.json').unlink()


def recover(config, state):
    from compact_runtime import up, compose
    pending = state / 'database/pending.json'
    if not pending.is_file() or pending.is_symlink():
        raise ValueError('no regular pending database journal')
    journal = json.loads(pending.read_text())
    if not re.fullmatch(r'[a-f0-9]{32}', journal.get('id', '')):
        raise ValueError('invalid database journal')
    before, after = validate(journal['before']), validate(journal['after'])
    if journal['phase'] not in ('stopping', 'upgrading', 'activating'):
        raise ValueError('unknown database recovery phase')
    # Stop only containers with this exact migration name AND ownership label.
    ids = output(['docker', 'ps', '-q', '--filter', 'label=' + LABEL + '=1']).split()
    for item in json.loads(output(['docker', 'inspect', *ids])) if ids else []:
        if item['Name'].startswith('/' + PROJECT + '-db-migrate-' + journal['id'] + '-'):
            stop_stage(item['Id'])
    value = after if journal['phase'] == 'activating' else before
    compose('stop', '--timeout', '60')
    inspect_volume(value['volume']); consumers(value['volume'], stopped=True)
    atomic(state / 'database/selection.json', value)
    up(config, database_transaction=True)
    finish(state, journal, 'recovered-new' if value == after else 'recovered-original')
    print('Recovery passed; source and migration volumes are retained. No binary downgrade performed.')
