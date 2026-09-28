"""Apply reviewed minimum Go versions in disposable build trees, never worktrees."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


def version(value):
    match = re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value or '')
    if not match:
        raise ValueError('dependency floor comparison requires stable vMAJOR.MINOR.PATCH versions')
    return tuple(map(int, match.groups()))


def read_modules(raw):
    decoder = json.JSONDecoder()
    modules = {}
    while raw.strip():
        item, end = decoder.raw_decode(raw.lstrip())
        raw = raw.lstrip()[end:]
        if item['Path'] in modules:
            raise ValueError('duplicate module identity')
        modules[item['Path']] = item
    return modules


def upgrades(modules, policy, root=None):
    if policy.get('schema_version') != 1 or not policy.get('modules'):
        raise ValueError('unsupported or empty dependency policy')
    selected = []
    for name, minimum in policy['modules'].items():
        if not re.fullmatch(r'[a-zA-Z0-9./_-]+', name):
            raise ValueError('invalid module path')
        wanted = version(minimum)
        if name not in modules:
            continue
        item = modules[name]
        # A root source checkout has no module version. Its provenance is the
        # source snapshot/locked commit, not an external dependency floor.
        if item.get('Main') is True and not item.get('Version'):
            directory = Path(item.get('Dir', ''))
            if root is not None and directory.is_absolute() and directory.resolve() == Path(root).resolve():
                continue
            raise ValueError(f'{name}: workspace-local dependency requires review; source preserved')
        if item.get('Replace'):
            raise ValueError(f'{name}: custom replacement requires dependency review; source preserved')
        if version(item.get('Version')) < wanted:
            selected.append(name + '@' + minimum)
    # One narrowly reviewed license-bearing commit preserves the pre-slog API.
    # Unknown pseudo versions/replacements are not silently normalized. Newer
    # stable custom dependencies remain the developer's deliberate selection.
    for name, pin in policy.get('license_pins', {}).items():
        if (not re.fullmatch(r'[a-zA-Z0-9./_-]+', name)
                or set(pin) != {'from', 'to', 'newer_stable'}
                or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+-0\.[0-9]{14}-[a-f0-9]{12}', pin['to'])):
            raise ValueError('invalid reviewed license pin')
        version(pin['from']); newer = version(pin['newer_stable'])
        if name not in modules:
            continue
        item = modules[name]
        if item.get('Replace') or item.get('Main'):
            raise ValueError(f'{name}: local/replaced license dependency requires review; source preserved')
        current = item.get('Version')
        if current == pin['from']:
            selected.append(name + '@' + pin['to'])
        elif current != pin['to'] and version(current) < newer:
            raise ValueError(f'{name}: unreviewed license version; source preserved')
    return selected


def apply(directory, policy_path, go='go', env=None):
    policy = json.loads(Path(policy_path).read_text())
    def graph():
        return read_modules(subprocess.check_output([go, 'list', '-mod=readonly', '-m', '-json', 'all'],
                                                     cwd=directory, env=env, text=True))
    selected = upgrades(graph(), policy, directory)
    if selected:
        subprocess.run([go, 'get', *selected], cwd=directory, env=env, check=True)
        subprocess.run([go, 'mod', 'tidy'], cwd=directory, env=env, check=True)
    if upgrades(graph(), policy, directory):
        raise ValueError('effective dependency graph did not meet reviewed minimum versions')
    return selected


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('policy', type=Path)
    args = parser.parse_args()
    apply(Path.cwd(), args.policy)
