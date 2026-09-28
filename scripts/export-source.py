#!/usr/bin/env python3
"""Export an immutable, history-free source candidate; never publish it.

This artifact boundary is not a substitute for Gitleaks, privacy review or
license review. GitHub metadata and old branches are deliberately not copied.
"""

import argparse
import ast
import gzip
import hashlib
import io
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import struct
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_DIRS = {'.git', '.lab', 'worktrees', '.cache', '.venv', '__pycache__', 'artifacts', 'bin',
                'dist', 'downloads', 'secrets', 'vendor', 'node_modules'}
BLOCKED_SUFFIXES = {'.qcow2', '.iso', '.img', '.vmdk', '.ova', '.ovf', '.ko',
                    '.exe', '.dll', '.so', '.a', '.o', '.pyc', '.db', '.sqlite',
                    '.pcap', '.pcapng', '.tar', '.gz', '.zip', '.pem', '.key', '.log',
                    '.sqlite3', '.p12', '.pfx'}
POLICY = 'config/public-source.json'
CAPTURE_POLICY = 'config/public-pcaps.json'
REQUIRED = {'LICENSE', 'THIRD_PARTY_NOTICES.md', POLICY, 'scripts/export-source.py'}


def capture_inventory(names, read):
    """Optional, exact reviewed-binary exceptions; old text-only commits still work."""
    if CAPTURE_POLICY not in names:
        return {}
    policy = json.loads(read(CAPTURE_POLICY))
    if (not isinstance(policy, dict) or set(policy) != {'version', 'captures'}
            or policy['version'] != 1 or not isinstance(policy['captures'], list)):
        raise ValueError('Invalid reviewed capture inventory')
    result = {}
    for entry in policy['captures']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'sha256', 'bytes', 'packets'}:
            raise ValueError('Invalid reviewed capture entry')
        name = entry['path']
        if (not isinstance(name, str)
                or not re.fullmatch(r'examples/pcap/[a-z0-9-]+/[a-z0-9-]+\.pcap', name)
                or name in result or name not in names
                or not isinstance(entry['sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
                or type(entry['bytes']) is not int or not 24 < entry['bytes'] <= 1_048_576
                or type(entry['packets']) is not int or not 0 < entry['packets'] <= 10000):
            raise ValueError('Invalid reviewed capture bounds, path or digest')
        if not all(str(PurePosixPath(name).parent / doc) in names
                   for doc in ('README.md', 'README.ja.md')):
            raise ValueError('Reviewed captures require bilingual documentation')
        result[name] = entry
    actual = {name for name in names if PurePosixPath(name).suffix.lower() in {'.pcap', '.pcapng'}}
    if actual != set(result):
        raise ValueError('Every public capture requires an exact reviewed entry')
    return result


def check_capture(content, entry, denylist=()):
    """Verify previously reviewed bytes, not automatically approve new traffic.

    Only classic little-endian, microsecond Ethernet PCAP is supported. There
    are no pcapng name-resolution, interface-description or secrets blocks.
    """
    if len(content) != entry['bytes'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
        raise ValueError('Reviewed capture digest/size mismatch; new review required')
    if (len(content) < 24 or content[:4] != b'\xd4\xc3\xb2\xa1'
            or struct.unpack_from('<HHII', content, 4) != (2, 4, 0, 0)):
        raise ValueError('Only reviewed classic Ethernet PCAP is supported')
    snaplen, linktype = struct.unpack_from('<II', content, 16)
    if not 0 < snaplen <= 262144 or linktype != 1:
        raise ValueError('Unexpected PCAP snaplen or link type')
    offset, packets, previous = 24, 0, -1
    while offset < len(content):
        if len(content) - offset < 16:
            raise ValueError('Incomplete PCAP record')
        seconds, micros, captured, original = struct.unpack_from('<IIII', content, offset)
        offset += 16
        timestamp = seconds * 1_000_000 + micros
        if (micros >= 1_000_000 or timestamp < previous or captured != original
                or not 14 <= captured <= snaplen or offset + captured > len(content)):
            raise ValueError('Invalid, truncated or unordered PCAP record')
        previous = timestamp
        packets += 1
        offset += captured
    if packets != entry['packets']:
        raise ValueError('Reviewed capture packet count mismatch')
    lowered = content.lower()
    for marker in denylist:
        needles = [marker.lower().encode()]
        try:
            needles.append(ipaddress.ip_address(marker).packed)
        except ValueError:
            pass
        if any(needle in (content if i else lowered) for i, needle in enumerate(needles)):
            raise ValueError('Private identity in capture (matched value redacted)')
    if re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', content):
        raise ValueError('Private key in capture (matched value redacted)')


def check_payload(name, content, captures, denylist):
    if any(marker.casefold() in name.casefold() for marker in denylist):
        raise ValueError('Private identity in source path (matched value redacted)')
    if name in captures:
        check_capture(content, captures[name], denylist)
    else:
        check_member(tarfile.TarInfo(name), content)
        check_privacy(name, content, denylist)


def read_policy(content):
    policy = json.loads(content)
    if not isinstance(policy, dict) or set(policy) != {'version', 'files', 'private_only'}:
        raise ValueError('Invalid public-source policy fields')
    if policy['version'] != 1:
        raise ValueError('Unsupported public-source policy version')
    for key in ('files', 'private_only'):
        paths = policy[key]
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError('Source policy paths must be lists of strings')
        if len(paths) != len(set(paths)):
            raise ValueError('Duplicate source policy path')
        for name in paths:
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or '..' in path.parts
                    or str(path) != name.rstrip('/') or '\\' in name
                    or (key == 'files' and name.endswith('/'))):
                raise ValueError('Source policy paths must be canonical relative paths')
    if not REQUIRED <= set(policy['files']):
        raise ValueError('Public-source policy must retain licenses and its own tooling')
    if any(is_private(name, policy) for name in policy['files']):
        raise ValueError('Public and private source paths overlap')
    return policy


def is_private(name, policy):
    return any(name == entry or (entry.endswith('/') and name.startswith(entry))
               for entry in policy['private_only'])


def select_files(names, policy):
    names, selected = set(names), set(policy['files'])
    missing = selected - names
    unknown = {name for name in names - selected if not is_private(name, policy)}
    if missing or unknown:
        # Print paths only, never source contents or identity denylist values.
        raise ValueError(f'Source inventory mismatch; missing={sorted(missing)}, '
                         f'unclassified={sorted(unknown)}')
    return sorted(selected)


def read_denylist(path):
    if path is None:
        return []
    values = json.loads(path.read_text())
    if (not isinstance(values, list) or not values
            or not all(isinstance(value, str) and value.strip() for value in values)):
        raise ValueError('External identity denylist must be a nonempty JSON string array')
    return values


def literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_string(node.left), literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def check_privacy(name, content, denylist):
    if any(marker.casefold() in name.casefold() for marker in denylist):
        raise ValueError('Private identity in source path (matched value redacted)')
    text = content.decode('utf-8')
    candidates = [text]
    if name.endswith('.py'):
        try:
            tree = ast.parse(text)
        except SyntaxError as error:
            raise ValueError(f'Invalid Python source: {name}') from error
        # Also inspect constant concatenation, including the former test bug.
        candidates.extend(value for node in ast.walk(tree)
                          if (value := literal_string(node)) is not None)
    for value in candidates:
        if any(marker.casefold() in value.casefold() for marker in denylist):
            raise ValueError(f'Private identity content: {name} (matched value redacted)')


def check_tree(root, denylist=()):
    root = root.resolve()
    policy = read_policy((root / POLICY).read_text())
    if (root / '.git').exists():
        # Includes new, nonignored files; ignored local/runtime data stays private.
        names = git(root, 'ls-files', '-z', '--cached', '--others',
                    '--exclude-standard').decode().split('\x00')
        names = [name for name in names if name]
    else:
        # Archive check: reject every extra file, even under a private-only path.
        names = [str(path.relative_to(root)) for path in root.rglob('*')
                 if path.is_symlink() or path.is_file()]
        unexpected = set(names) - set(policy['files'])
        if unexpected:
            raise ValueError(f'Unexpected archive paths: {sorted(unexpected)}')
    files = select_files(names, policy)
    captures = capture_inventory(files, lambda name: (root / name).read_bytes())
    for name in files:
        path = root / name
        if (path.is_symlink() or not path.is_file()
                or any(parent.is_symlink() for parent in path.parents if parent != root)):
            raise ValueError(f'Only ordinary source files are allowed: {name}')
        content = path.read_bytes()
        check_payload(name, content, captures, denylist)
    return {'files': len(files), 'identity_denylist_applied': bool(denylist),
            'publication_approved': False}


def git(repo, *args):
    return subprocess.check_output(['git', '--no-replace-objects', '-C', str(repo), *args],
                                   stderr=subprocess.PIPE)


def check_history(repo, denylist=()):
    """Check all locally reachable refs, not only the selected branch's tip.

    This intentionally fails on the private recovery repository. It does not
    fetch remote refs or inspect GitHub PR caches, logs, artifacts or reflogs.
    Diagnostics expose categories/counts only, never matched identities.
    """
    repo = repo.resolve()
    if Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != repo:
        raise ValueError('History check requires the repository top-level directory')
    if git(repo, 'rev-parse', '--is-shallow-repository').strip() == b'true':
        raise ValueError('Shallow history cannot be approved; use a complete isolated clone')
    grafts = Path(git(repo, 'rev-parse', '--git-path', 'info/grafts').decode().strip())
    if not grafts.is_absolute():
        grafts = repo / grafts
    if grafts.exists():
        raise ValueError('Grafts can hide history; this repository requires separate review')
    refs = git(repo, 'for-each-ref', '--format=%(refname) %(objectname)').decode().splitlines()
    if any(line.startswith('refs/replace/') for line in refs):
        raise ValueError('Replace refs require separate review')
    issues = {}

    def issue(category):
        issues[category] = issues.get(category, 0) + 1

    def privacy(label, data):
        try:
            check_privacy(label, data, denylist)
        except (ValueError, UnicodeError):
            issue('private_or_nontext_metadata')

    checked_tags = set()
    for line in refs:
        name, object_id = line.split(' ', 1)
        privacy('ref metadata', name.encode())
        # Also rejects unusual refs pointing directly to blobs/trees.
        try:
            git(repo, 'rev-parse', '--verify', name + '^{commit}')
        except subprocess.CalledProcessError as error:
            raise ValueError('A non-commit ref requires separate review (ref name redacted)') from error
        while git(repo, 'cat-file', '-t', object_id).strip() == b'tag':
            if object_id in checked_tags:
                break
            checked_tags.add(object_id)
            content = git(repo, 'cat-file', 'tag', object_id)
            privacy('tag metadata', content)
            object_id = content.splitlines()[0].split(b' ', 1)[1].decode()
    commits = git(repo, 'rev-list', '--all', 'HEAD').decode().splitlines()
    checked_blobs = set()
    for commit in commits:
        privacy('commit metadata', git(repo, 'cat-file', 'commit', commit))
        entries = {}
        for entry in git(repo, 'ls-tree', '-r', '-z', commit).split(b'\x00'):
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            mode, kind, blob = metadata.decode().split()
            entries[name.decode()] = (mode, kind, blob)
        try:
            policy = read_policy(git(repo, 'show', f'{commit}:{POLICY}'))
            # Unlike export, a publishable Git history may not retain the
            # private-only files even when they have been explicitly classified.
            if set(entries) != set(policy['files']):
                issue('commit_contains_nonpublic_or_missing_paths')
        except (ValueError, subprocess.CalledProcessError):
            issue('commit_without_valid_public_inventory')
        try:
            captures = capture_inventory(entries, lambda name: git(repo, 'show', f'{commit}:{name}'))
        except (ValueError, subprocess.CalledProcessError):
            issue('invalid_capture_inventory')
            captures = {}
        for name, (mode, kind, blob) in entries.items():
            review = json.dumps(captures.get(name), sort_keys=True)
            if (name, blob, mode, review) in checked_blobs:
                continue
            checked_blobs.add((name, blob, mode, review))
            if kind != 'blob' or mode not in ('100644', '100755'):
                issue('nonregular_source_entry')
                continue
            content = git(repo, 'cat-file', 'blob', blob)
            try:
                check_payload(name, content, captures, denylist)
            except (ValueError, UnicodeError):
                issue('forbidden_or_private_source_version')
    return {'commits': len(commits), 'refs': len(refs), 'annotated_tags': len(checked_tags),
            'unique_path_blob_versions': len(checked_blobs), 'issues': issues,
            'history_matches_public_source_policy': not issues,
            'identity_denylist_applied': bool(denylist), 'publication_approved': False,
            'scope': 'Local refs and HEAD only; no remote metadata, caches or reflogs'}


def check_member(member, content):
    path = PurePosixPath(member.name)
    if (path.is_absolute() or '..' in path.parts
            or any(p in BLOCKED_DIRS for p in path.parts)
            or 'infra/cloud-init/generated/' in str(path)):
        raise ValueError(f'Forbidden source path: {member.name}')
    if not member.isfile():
        raise ValueError(f'Only ordinary source files are allowed: {member.name}')
    name = path.name
    if (path.suffix.lower() in BLOCKED_SUFFIXES or name.endswith(('.local.yml', '.local.yaml'))
            or name in {'host.env', 'lab.local.yaml', 'id_rsa', 'id_ed25519'}
            or (name.startswith('.env') and name != '.env.example')):
        raise ValueError(f'Runtime, local or credential file is forbidden: {member.name}')
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError(f'Non-text source member: {member.name}') from error
    if '\x00' in text or re.search(r'-----BEGIN [A-Z ]*PRIVATE KEY-----', text):
        raise ValueError(f'Binary or private-key content: {member.name}')


def export(repo, revision, output, denylist=()):
    commit = git(repo, 'rev-parse', '--verify', '--end-of-options',
                 revision + '^{commit}').decode().strip()
    policy = read_policy(git(repo, 'show', f'{commit}:{POLICY}'))
    entries = {}
    for entry in git(repo, 'ls-tree', '-r', '-z', commit).split(b'\x00'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        if not entry.startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError('Symlinks and submodules require a separate release review')
        mode, _, blob = metadata.decode().split()
        entries[name.decode()] = (mode, blob)
    selected = select_files(entries, policy)
    captures = capture_inventory(selected, lambda name: git(repo, 'show', f'{commit}:{name}'))
    raw = io.BytesIO()
    # Construct the selected snapshot only: no Git PAX commit ID, authors,
    # host UID/GID, timestamps or private-only directory entries.
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as target:
        for name in selected:
            mode, blob = entries[name]
            content = git(repo, 'cat-file', 'blob', blob)
            member = tarfile.TarInfo('free5gc-srv6-mup-lab/' + name)
            member.mode = 0o755 if mode == '100755' else 0o644
            member.size = len(content)
            check_payload(name, content, captures, denylist)
            target.addfile(member, io.BytesIO(content))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=compressed, mtime=0) as stream:
        stream.write(raw.getvalue())
    payload = compressed.getvalue()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create protects previous candidates, even if names collide.
    with output.open('xb') as destination:
        destination.write(payload)
    return {'revision': commit, 'archive': str(output), 'files': len(selected),
            'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload),
            'identity_denylist_applied': bool(denylist),
            'publication_approved': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', default='HEAD', help='Committed source only; uncommitted files are excluded')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path, help='New local archive; existing files are never overwritten')
    mode.add_argument('--check-tree', type=Path, help='Validate a working tree or freshly extracted candidate')
    mode.add_argument('--check-history', type=Path, help='Read-only check of complete local refs, all committed trees and metadata')
    parser.add_argument('--denylist', type=Path, help='Private JSON identity list, never included in distribution')
    args = parser.parse_args()
    try:
        denylist = read_denylist(args.denylist)
        if args.check_history is not None:
            result = check_history(args.check_history, denylist)
        elif args.check_tree is not None:
            result = check_tree(args.check_tree, denylist)
        else:
            result = export(ROOT, args.revision, args.output, denylist)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Source export refused: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get('history_matches_public_source_policy', True) else 1


if __name__ == '__main__':
    sys.exit(main())
