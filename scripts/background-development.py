#!/usr/bin/env python3
"""Inspect and control the optional, initially paused background developer."""
import argparse
import json
import os
from pathlib import Path
import sys

from background_development import POLICY, Store, load_policy, plan, resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, default=POLICY)
    parser.add_argument('--state', type=Path, default=Path(os.environ.get(
        'XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'srv6-mup-background')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('plan', 'status', 'pause', 'resume', 'run'):
        command = sub.add_parser(action)
        if action == 'run':
            command.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    policy = load_policy(args.policy)
    store = Store(args.state)
    state = store.read()
    if args.action in ('plan', 'status') or args.action == 'run' and args.dry_run:
        data = plan(policy, state, resources(args.state))
        data['tasks'] = state['tasks']
        data['last_result'] = state['last_result']
        data['note'] = 'Eligibility alone does not attest installation, authentication or lab recovery.'
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    if args.action == 'run':
        from background_executor import run
        run(store, policy)
        return
    with store.locked():
        state = store.read()
        if args.action == 'resume':
            from background_executor import readiness
            readiness(store, policy)
            if state.get('active') or state['failures'] >= policy['limits']['consecutive_failures']:
                raise ValueError('interrupted run/failure limit requires operator recovery before resume')
        state['paused'] = args.action == 'pause'
        store.save(state)
    print('Paused; an already running operation must finish its recovery.' if state['paused'] else 'Resumed.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        # No captured command output, credentials, runtime state or filenames.
        print('Background operation refused: ' + str(error), file=sys.stderr)
        sys.exit(1)
