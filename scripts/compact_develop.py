"""Editable source snapshots, VM-local builders and recoverable image activation."""
from __future__ import annotations

import hashlib
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tarfile
import uuid

from compact_config import ROOT, locks
from compact_vm import digest, output, run, write

OWN = {'mup-controller': ['mupc'], 'pfcp-observer': ['observer'],
       'mupctl': ['mupc'], 'mup-dashboard': ['dashboard']}
NF_NAMES = ('amf', 'ausf', 'chf', 'nrf', 'nssf', 'pcf', 'smf', 'udm', 'udr', 'upf', 'webui')
COMPONENTS = {**OWN, 'vinbero': ['tpe', 'npe'], 'ueransim': ['ran', 'ue'],
              **{'free5gc-' + name: ['free5gc-' + name] for name in NF_NAMES}}
STATE = ROOT / '.lab/runtime'
DEV = STATE / 'development'
BLOCKED = {'.git', '.lab', '.ssh', 'secrets', 'node_modules', '__pycache__',
           'cmake-build-release', 'cmake-build-debug', 'build', 'bin', 'dist', 'logs'}
PRIVATE_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.pcap', '.pcapng', '.qcow2', '.local.yml', '.local.yaml')


def component(name):
    if name not in COMPONENTS:
        raise ValueError('unknown development component')
    return COMPONENTS[name]


def source_spec(name):
    component(name)
    return locks()['compact_nf_sources'][name.removeprefix('free5gc-')] if name.startswith('free5gc-') else locks()['sources'][name]


def source(name):
    component(name)
    if name in OWN:
        return ROOT
    directory = ROOT / '.lab/source-records'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'operation.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return external_source(name)


def external_source(name):
    spec = source_spec(name)
    path = ROOT / 'worktrees' / name
    marker = ROOT / '.lab/source-records' / (name + '.json')
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('refusing symlink development worktree')
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        run(['git', 'init', '-q', path])
        run(['git', '-C', path, 'remote', 'add', 'origin', spec['repository']])
        run(['git', '-C', path, 'fetch', '--depth=1', 'origin', spec['commit']])
        run(['git', '-C', path, 'checkout', '--detach', 'FETCH_HEAD'])
        if name == 'vinbero':
            patch = ROOT / 'third_party/vinbero/patches/0001-gobgp-v4.8-mup-draft01.patch'
            run(['git', '-C', path, 'apply', '--check', patch])
            run(['git', '-C', path, 'apply', patch])
        marker.parent.mkdir(parents=True, exist_ok=True)
        write(marker, json.dumps(spec))
    if not marker.is_file() or json.loads(marker.read_text()) != spec:
        raise ValueError('unowned or changed source lock; existing worktree is preserved')
    if output(['git', '-C', path, 'remote', 'get-url', 'origin']) != spec['repository']:
        raise ValueError('source origin differs from lock; worktree is preserved')
    # Local commits and uncommitted edits are allowed; never checkout/reset them.
    run(['git', '-C', path, 'merge-base', '--is-ancestor', spec['commit'], 'HEAD'])
    return path


def safe_source_name(name):
    path = PurePosixPath(name)
    return (bool(path.parts) and not path.is_absolute() and path.as_posix() == name and '..' not in path.parts
            and not set(path.parts) & BLOCKED and '\\' not in name
            and not any(part == '.env' or part.startswith('.env.') for part in path.parts)
            and not name.lower().endswith(PRIVATE_SUFFIXES))


def snapshot(name, destination, root=None):
    root = source(name) if root is None else root
    if name in OWN:
        paths = [root / 'go.mod', root / 'go.sum']
        for directory in ('api', 'cmd', 'internal'):
            paths.extend((root / directory).rglob('*'))
    else:
        names = subprocess.check_output(['git', '-C', root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'])
        paths = [root / name.decode() for name in names.split(b'\0') if name]
    files = {}
    for path in sorted(set(paths)):
        name_in_source = path.relative_to(root).as_posix()
        if not safe_source_name(name_in_source):
            continue
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
            raise ValueError('symlink source entries are not supported')
        if path.is_dir() or not path.exists():
            continue
        if not path.is_file():
            raise ValueError('source entry is not a regular file')
        files[name_in_source] = {'sha256': digest(path), 'mode': 0o755 if path.stat().st_mode & 0o111 else 0o644}
    if not files:
        raise ValueError('empty development source')
    manifest = {'component': name, 'files': files,
                'source_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                # Public source archives deliberately contain no Git history.
                # File hashes remain authoritative for those editable copies.
                'head': output(['git', '-C', root, 'rev-parse', 'HEAD']) if (root / '.git').exists() else None,
                'base': None if name in OWN else source_spec(name)}
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(destination, 'w') as archive:
        data = json.dumps(manifest).encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(data); info.mode = 0o600
        archive.addfile(info, io.BytesIO(data))
        for filename, spec in files.items():
            data = (root / filename).read_bytes()
            if hashlib.sha256(data).hexdigest() != spec['sha256']:
                raise ValueError('source changed while snapshotting; retry after saving edits')
            info = tarfile.TarInfo('source/' + filename)
            info.size = len(data); info.mode = spec['mode']
            archive.addfile(info, io.BytesIO(data))
    destination.chmod(0o600)
    return manifest


def host_rebuild(vm, name, clean_runtime=False, local_builder=False):
    component(name)
    if clean_runtime and not name.startswith('free5gc-'):
        raise ValueError('clean runtime is supported only for free5GC NF/WebUI components')
    vm.owned()
    build_id = uuid.uuid4().hex
    archive = vm.state / 'builds' / build_id / 'source.tar'
    metadata = snapshot(name, archive)
    print(f'Source snapshot: {metadata["source_sha256"]}; local edits preserved.', flush=True)
    vm.sync()
    guest_dir = f'/opt/srv6-mup-compact/.lab/runtime/development/builds/{build_id}'
    vm.ssh('sudo install -d -m 0700 ' + guest_dir)
    with archive.open('rb') as stream:
        vm.ssh('sudo sh -c ' + shlex.quote('umask 077; tee ' + guest_dir + '/source.tar >/dev/null'), stdin=stream)
    vm.runtime('rebuild', name, build_id, *(['--clean-runtime'] if clean_runtime else []),
               *(['--local-builder'] if local_builder else []))


def unpack(path, name):
    component(name)
    with tarfile.open(path / 'source.tar') as archive:
        members = archive.getmembers()
        if len(members) > 20000 or sum(item.size for item in members) > 512 << 20:
            raise ValueError('source archive exceeds limits')
        if len({item.name for item in members}) != len(members):
            raise ValueError('duplicate archive entry')
        for item in members:
            if not item.isfile() or (item.name != 'manifest.json' and
                (not item.name.startswith('source/') or not safe_source_name(item.name[7:]))):
                raise ValueError('unsafe source archive entry')
        manifest = json.load(archive.extractfile('manifest.json'))
        if manifest['component'] != name or set(manifest['files']) != {m.name[7:] for m in members if m.name != 'manifest.json'}:
            raise ValueError('source manifest mismatch')
        if hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest() != manifest['source_sha256']:
            raise ValueError('source manifest digest mismatch')
        directory = path / 'source'
        if directory.exists():
            raise ValueError('build source was already extracted; use a new build ID')
        directory.mkdir(mode=0o755)
        for item in members:
            if item.name == 'manifest.json':
                continue
            spec = manifest['files'][item.name[7:]]
            data = archive.extractfile(item).read()
            if hashlib.sha256(data).hexdigest() != spec['sha256'] or spec['mode'] not in (0o644, 0o755):
                raise ValueError('source file digest/mode mismatch')
            target = path / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data); target.chmod(spec['mode'])
    atomic(path / 'source-manifest.json', manifest)
    return manifest


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    write(temporary, json.dumps(value, indent=2))
    os.replace(temporary, path)


def state_file(name, default):
    path = DEV / name
    return json.loads(path.read_text()) if path.exists() else default


def overrides():
    values = state_file('overrides.json', {})
    allowed = {service for services in COMPONENTS.values() for service in services}
    if not isinstance(values, dict) or any(key not in allowed or not re.fullmatch(r'sha256:[a-f0-9]{64}', value)
                                         for key, value in values.items()):
        raise ValueError('invalid development image override')
    return values


def builder(local=False):
    from compact_runtime import fetch
    from compact_release import builder_inputs, release_builder
    recipe = ROOT / 'containers/compact-builder.Dockerfile'
    desired = builder_inputs()
    candidate_path = STATE / 'candidate.json'
    candidate = json.loads(candidate_path.read_text()) if candidate_path.exists() else {}
    if not local and 'release_manifest' in candidate:
        record = release_builder(candidate, desired)
        atomic(DEV / 'builder.json', record)
        return record
    previous = state_file('builder.json', {})
    if previous.get('inputs') == desired and not previous.get('reference'):
        output(['docker', 'image', 'inspect', previous['image_id']])
        return previous
    context = DEV / 'builder-context'
    context.mkdir(parents=True, exist_ok=True)
    for key, filename in [('go', 'go.tar.gz'), ('node', 'node.tar.xz'), ('yarn', 'yarn.js')]:
        spec = desired['toolchains'][key]
        fetch(spec['url'], context / filename, spec['sha256'])
    tag = 'srv6-mup-compact-builder:' + hashlib.sha256(json.dumps(desired, sort_keys=True).encode()).hexdigest()[:24]
    args = ['docker', 'build', '-f', recipe, '-t', tag, '--build-arg', 'BUILDER_BASE=' + desired['base']]
    args += ['--build-arg', 'LIBC_HEADERS_VERSION=' + desired['linux_libc_dev']]
    for key in ('go', 'node', 'yarn'):
        args += ['--build-arg', key.upper() + '_SHA256=' + desired['toolchains'][key]['sha256']]
    run([*args, context])
    record = {'inputs': desired, 'image_id': output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag])}
    record['packages'] = output(['docker', 'run', '--rm', '--network', 'none', '--cap-drop=ALL',
                                record['image_id'], 'dpkg-query', '-W'])
    atomic(DEV / 'builder.json', record)
    return record


def build_script(name):
    component(name)
    preamble = '''#!/bin/bash
set -euo pipefail
export TMPDIR=/scratch/tmp
export XDG_CACHE_HOME=/cache/xdg XDG_CONFIG_HOME=/scratch/config
export NPM_CONFIG_CACHE=/cache/npm
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" /scratch/src
cp -a /source/. /scratch/src/
cd /scratch/src
'''
    if name == 'vinbero' or name.startswith('free5gc-'):
        preamble += 'go mod tidy\npython3 /run/dependencies.py /run/dependencies.json\n'
    flags = '-p 2 -trimpath -buildvcs=false'
    if name in OWN:
        script = f'go test -mod=readonly -p 2 ./...\ngo build -mod=readonly {flags} -o /out/{name} ./cmd/{name}\n'
    elif name == 'vinbero':
        script = '''export BPF_CLANG=clang
export BPF_CFLAGS='-O2 -g -Wall -Werror -fdebug-prefix-map=/scratch/src=/src/vinbero'
go generate ./pkg/bpf
go mod tidy
go test -p 2 ./pkg/bgp/gobgp -run MUP -count=1
'''
        script += '\n'.join(f'go build {flags} -o /out/{binary} ./cmd/{binary}' for binary in ('vinberod', 'vinbero')) + '\n'
    elif name == 'ueransim':
        script = 'make -j2\ncp build/nr-gnb build/nr-ue build/nr-cli /out/\n'
    elif name == 'free5gc-webui':
        script = f'''go test -mod=readonly -p 2 -run '^$' ./...
go build -mod=readonly {flags} -o /out/webui ./server.go
cd frontend
export YARN_ENABLE_TELEMETRY=0 YARN_CACHE_FOLDER=/provenance/frontend-cache YARN_GLOBAL_FOLDER=/cache/yarn-global
export YARN_ENABLE_GLOBAL_CACHE=0 YARN_ENABLE_IMMUTABLE_CACHE=0
node /opt/yarn.js install --immutable
node /opt/yarn.js build
cp -a build /out/public
mkdir -p /provenance/frontend-locks
cp package.json yarn.lock .yarnrc.yml /provenance/frontend-locks/
'''
    else:
        binary = name.removeprefix('free5gc-')
        script = f"go test -mod=readonly -p 2 -run '^$' ./...\ngo build -mod=readonly {flags} -o /out/{binary} ./cmd/main.go\n"
    # Preserve effective module locks, including Vinbero's generated changes.
    script += '\ncd /scratch/src\nif [ -f go.mod ]; then cp go.mod go.sum /provenance/; fi\n'
    return preamble + script


def builder_command(path, image_id):
    return ['docker', 'run', '--rm', '--init', '--user', '65534:65534', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--read-only', '--memory=3g', '--cpus=2', '--pids-limit=512',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
            '-v', f'{path / "source"}:/source:ro', '-v', f'{path / "scratch"}:/scratch:rw',
            '-v', f'{path / "output"}:/out:rw', '-v', f'{path / "provenance"}:/provenance:rw',
            '-v', f'{DEV / "cache"}:/cache:rw', '-v', f'{path / "build.sh"}:/run/build.sh:ro',
            '-v', f'{ROOT / "scripts/compact_dependencies.py"}:/run/dependencies.py:ro',
            '-v', f'{ROOT / "config/compact-dependencies.json"}:/run/dependencies.json:ro',
            image_id, 'bash', '/run/build.sh']


def resolve_image(name, selected):
    from compact_runtime import GENERATED
    import yaml
    # The rendered deployment captures the common M1 image and pinned upstream
    # images; custom replacements are immutable Docker image IDs.
    model = yaml.safe_load((GENERATED / 'compose.yml').read_text())
    values = {selected.get(service, model['services'][service]['image']) for service in component(name)}
    ids = {output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image]) for image in values}
    if len(ids) != 1:
        raise ValueError('component services have diverged parent images')
    return ids.pop()


def rebuild(config, name, build_id, clean_runtime=False, local_builder=False):
    component(name)
    if clean_runtime and not name.startswith('free5gc-'):
        raise ValueError('clean runtime is supported only for free5GC NF/WebUI components')
    if not re.fullmatch('[a-f0-9]{32}', build_id or ''):
        raise ValueError('invalid build ID')
    if (DEV / 'pending.json').exists():
        raise ValueError('unfinished activation; run up to recover before rebuilding')
    path = DEV / 'builds' / build_id
    parent = resolve_image(name, overrides())
    record = build_image(name, path, parent, clean_runtime, local_builder=local_builder)
    image_id = record['image_id']
    before = overrides()
    after = dict(before)
    after.update({service: image_id for service in component(name)})
    history = state_file('history.json', [])
    event = {'component': name, 'build_id': build_id,
             'before': {s: before.get(s) for s in component(name)},
             'after': {s: after[s] for s in component(name)}}
    activate(config, before, after, history, history + [event], path)
    print(f'PASS: {name} built, activated and verified; build ID {build_id}', flush=True)


def provenance(path, name):
    """Retain the actual per-build frontend cache, not a shared mutable cache."""
    modules, frontend = {}, {}
    total = 0
    for file in (path / 'provenance').rglob('*'):
        relative = file.relative_to(path / 'provenance').as_posix()
        if file.is_symlink():
            raise ValueError('symlink provenance output')
        if file.is_dir():
            if name != 'free5gc-webui' or relative not in ('frontend-cache', 'frontend-locks'):
                raise ValueError('unexpected provenance directory')
            continue
        if not file.is_file():
            raise ValueError('nonregular provenance output')
        total += file.stat().st_size
        if total > 480 * 1024**2 or len(frontend) > 10000:
            raise ValueError('provenance output exceeds limits')
        if relative in ('go.mod', 'go.sum'):
            modules[relative] = digest(file)
        elif name == 'free5gc-webui' and (relative in ('frontend-locks/package.json', 'frontend-locks/yarn.lock',
                                                       'frontend-locks/.yarnrc.yml', 'frontend-cache/.gitignore')
                or re.fullmatch(r'frontend-cache/@?[a-zA-Z0-9][a-zA-Z0-9._-]*\.zip', relative)):
            frontend[relative] = {'sha256': digest(file), 'size': file.stat().st_size}
        else:
            raise ValueError('unexpected provenance output')
    if name == 'free5gc-webui' and (not any(p.endswith('.zip') for p in frontend)
            or not {'frontend-locks/package.json', 'frontend-locks/yarn.lock', 'frontend-locks/.yarnrc.yml'} <= frontend.keys()):
        raise ValueError('missing effective frontend locks/cache')
    result = {'effective_module_locks': modules, 'frontend_sources': frontend}
    if frontend:
        archive_path = path / 'frontend-sources.tar'
        with tarfile.open(archive_path, 'x') as archive:
            for relative, spec in sorted(frontend.items()):
                file = path / 'provenance' / relative
                if file.is_symlink() or file.stat().st_size != spec['size'] or digest(file) != spec['sha256']:
                    raise ValueError('frontend input changed while preserving build evidence')
                item = tarfile.TarInfo(relative); item.size = spec['size']; item.mode = 0o600
                with file.open('rb') as stream: archive.addfile(item, stream)
        archive_path.chmod(0o600)
        result['frontend_sources_archive_sha256'] = digest(archive_path)
    return result


def build_payload(name, path, *, local_builder=False):
    """Compile only a preserved snapshot in the builder; never activate anything."""
    component(name)
    manifest = unpack(path, name)
    build_env = builder(local=True) if local_builder else builder()
    for directory in ('scratch', 'output', 'provenance'):
        (path / directory).mkdir(); os.chown(path / directory, 65534, 65534)
    (DEV / 'cache').mkdir(parents=True, exist_ok=True); os.chown(DEV / 'cache', 65534, 65534)
    write(path / 'build.sh', build_script(name), 0o644)
    # No compose mutations or active-image writes occur before build success.
    try:
        run(builder_command(path, build_env['image_id']))
        files = [p for p in (path / 'output').rglob('*') if p.is_file()]
        if not files or any(p.is_symlink() for p in (path / 'output').rglob('*')):
            raise ValueError('empty or symlink build output')
        record = {'build_id': path.name, 'component': name,
                  'source_sha256': manifest['source_sha256'], 'builder': build_env,
                  'build_script_sha256': digest(path / 'build.sh'),
                  'dependency_policy_sha256': digest(ROOT / 'config/compact-dependencies.json'),
                  'dependency_implementation_sha256': digest(ROOT / 'scripts/compact_dependencies.py'),
                  **provenance(path, name),
                  'artifacts': {p.relative_to(path / 'output').as_posix(): digest(p) for p in files}}
        atomic(path / 'payload.json', record)
        return record
    except (ValueError, OSError, subprocess.SubprocessError):
        atomic(path / 'result.json', {'status': 'build-failed', 'active_images_unchanged': True})
        raise


def build_image(name, path, parent=None, clean_runtime=False, *, local_builder=False):
    """Build and record one artifact without reading or changing a deployment."""
    component(name)
    if clean_runtime and not name.startswith('free5gc-'):
        raise ValueError('clean runtime is supported only for free5GC NF/WebUI components')
    if not clean_runtime and parent is None:
        raise ValueError('a layered build requires a parent image')
    try:
        payload = build_payload(name, path, local_builder=local_builder)
        context = path / 'image'
        destination = context / 'payload' / ('free5gc' if name.startswith('free5gc-') else 'usr/local/bin')
        shutil.copytree(path / 'output', destination)
        write(context / 'Dockerfile', 'ARG PARENT\nFROM ${PARENT}\nCOPY payload/ /\n')
        build_args = [] if parent is None else ['--build-arg', 'PARENT=' + parent]
        if clean_runtime:
            write(context / 'Dockerfile', (ROOT / 'containers/compact-nf.Dockerfile').read_text())
            build_args = ['--build-arg', 'RUNTIME_BASE=' + locks()['compact']['runtime_base']]
        tag = 'srv6-mup-compact-dev:' + path.name
        run(['docker', 'build', *build_args, '-t', tag, context])
        image_id = output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag])
        runtime_parent = output(['docker', 'image', 'inspect', '--format', '{{.Id}}',
                                 locks()['compact']['runtime_base']]) if clean_runtime else parent
        record = {**payload, 'image_id': image_id,
                  'parent_image_id': runtime_parent, 'previous_image_id': parent,
                  'clean_runtime': clean_runtime,
                  'runtime_base': locks()['compact']['runtime_base'] if clean_runtime else parent,
                  'runtime_recipe_sha256': digest(context / 'Dockerfile')}
        atomic(path / 'build.json', record)
    except (ValueError, OSError, subprocess.SubprocessError):
        atomic(path / 'result.json', {'status': 'build-failed', 'active_images_unchanged': True})
        raise
    return record


def deploy(config, services):
    from compact_runtime import up, compose, dashboard_install, GENERATED, checkout, database_selection
    from compact_compose import render, candidate_selection
    if set(services) == {'dashboard'}:
        candidate = json.loads((STATE / 'candidate.json').read_text())
        render(config, checkout('free5gc_compose'), GENERATED, candidate['image_id'],
               candidate_selection(candidate, overrides()), database_selection())
        compose('config', '--quiet')
        dashboard_install()
    else:
        up(config, in_transaction=True)


def activate(config, before, after, old_history, new_history, path):
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    atomic(DEV / 'pending.json', {'before': before, 'history': old_history})
    try:
        atomic(DEV / 'overrides.json', after)
        deploy(config, changed)
        atomic(DEV / 'history.json', new_history)
        atomic(path / 'result.json', {'status': 'activated', 'verified': True})
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        atomic(DEV / 'overrides.json', before)
        atomic(DEV / 'history.json', old_history)
        try:
            deploy(config, changed)
        except (ValueError, OSError, subprocess.SubprocessError):
            atomic(path / 'result.json', {'status': 'activation-and-recovery-failed'})
            raise ValueError('activation and recovery failed; prior images selected, run up to recover') from error
        atomic(path / 'result.json', {'status': 'activation-failed', 'previous_images_restored': True})
        (DEV / 'pending.json').unlink()
        raise ValueError('activation failed; previous images restored and verified') from error
    (DEV / 'pending.json').unlink()


def rollback(config, name):
    component(name)
    if (DEV / 'pending.json').exists():
        raise ValueError('unfinished activation; run up to recover first')
    history = state_file('history.json', [])
    indexes = [i for i, event in enumerate(history) if event['component'] == name]
    if not indexes:
        raise ValueError('no successful component build to roll back')
    index = indexes[-1]; event = history[index]
    before = overrides()
    if any(before.get(key) != value for key, value in event['after'].items()):
        raise ValueError('a later component changed the same service; roll it back first')
    after = dict(before)
    for key, value in event['before'].items():
        if value is None:
            after.pop(key, None)
        else:
            after[key] = value
    path = DEV / 'rollbacks' / uuid.uuid4().hex
    activate(config, before, after, history, history[:index] + history[index+1:], path)
    print(f'PASS: {name} previous image restored and verified; sources are unchanged', flush=True)


def recover():
    path = DEV / 'pending.json'
    if path.exists():
        data = json.loads(path.read_text())
        atomic(DEV / 'overrides.json', data['before'])
        atomic(DEV / 'history.json', data['history'])
        path.rename(DEV / ('recovered-' + uuid.uuid4().hex + '.json'))
        print('Interrupted activation: restoring previous image selection before up.', flush=True)


def builds():
    selected = overrides()
    for path in sorted((DEV / 'builds').glob('*/source-manifest.json')):
        source_record = json.loads(path.read_text())
        directory = path.parent
        build = json.loads((directory / 'build.json').read_text()) if (directory / 'build.json').exists() else {}
        result = json.loads((directory / 'result.json').read_text()) if (directory / 'result.json').exists() else {'status': 'incomplete'}
        name = source_record['component']
        print(json.dumps({'build_id': directory.name, 'component': name,
            'source_sha256': source_record['source_sha256'], 'image_id': build.get('image_id'),
            'status': result['status'], 'active': bool(build) and
            all(selected.get(service) == build['image_id'] for service in component(name))}))
