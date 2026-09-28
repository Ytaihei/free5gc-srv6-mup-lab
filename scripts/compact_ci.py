#!/usr/bin/env python3
"""Opt-in dedicated-runner candidate build/test/audit. Never publish or delete."""
import argparse
import json
import re
import subprocess
import sys

from compact_config import ROOT
from compact_vm import run, write


def profile(run_id):
    if not re.fullmatch(r'[0-9]{1,16}-[0-9]{1,3}', run_id):
        raise ValueError('expected numeric CI run ID and attempt')
    return {'schema_version': 1,
            'vm': {'name': 'mup-ci-' + run_id, 'network': 'mup-ci-' + run_id,
                   'bridge': 'virbr-mupci', 'subnet': '192.168.130.0/24',
                   'gateway': '192.168.130.1', 'address': '192.168.130.10',
                   'mac': '52:54:00:5c:30:10'}, 'dashboard_port': 8790}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    args = parser.parse_args()
    config = profile(args.run_id)
    directory = ROOT / '.lab/ci' / args.run_id
    if (ROOT / '.lab' / config['vm']['name']).exists():
        raise ValueError('candidate VM state already exists; use a new run ID, never reuse a cache')
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    path = directory / 'profile.json'
    write(path, json.dumps(config))  # JSON is valid YAML; never copy local settings.
    command = [ROOT / 'lab', '--config', path]
    try:
        run([*command, 'doctor'])
        run([*command, 'up', '--build'])
        run([*command, 'rebuild', 'mup-controller', '--local-builder'])
        run([*command, 'test', 'one-call'])
        run([*command, 'rollback', 'mup-controller'])
        run([*command, 'test', 'all'])
        run([*command, 'audit-images', '--scan'])
    finally:
        # Retain the VM/disk, image cache, audit evidence and keys privately.
        # A new runner/workspace is required for another clean candidate job.
        if (ROOT / '.lab' / config['vm']['name'] / 'owner.json').exists():
            run([*command, 'down'])


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'ERROR: {error}')
