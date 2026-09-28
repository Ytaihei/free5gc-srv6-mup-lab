"""Read package metadata in every saved layer, never extract or run packages.

This covers recorded dpkg versions, not unrecorded/native payloads. Whiteouts
do not erase the source obligations of bytes retained in an earlier layer.
"""
import hashlib
import json
from pathlib import PurePosixPath
import re
import tarfile

NAME = r'[a-z0-9][a-z0-9+.-]+'
VERSION = r'[0-9][a-zA-Z0-9.+:~\-]*'
SOURCE = re.compile(rf'({NAME})(?: \(({VERSION})\))?')
MAX_STATUS = 16 * 1024**2


def safe_name(value):
    if value.startswith('./'):
        value = value[2:]
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value.rstrip('/'):
        raise ValueError('unsafe layer member path')
    return str(path)


def stanzas(raw):
    fields, key, count = {}, None, 0
    for line in raw.decode('utf-8').splitlines() + ['']:
        if not line.strip():
            if fields:
                count += 1
                if count > 10000:
                    raise ValueError('too many package metadata stanzas')
                yield fields
            fields, key = {}, None
        elif line[0] in ' \t':
            if key is None:
                raise ValueError('orphan package field continuation')
            fields[key] += '\n' + line.strip()
        else:
            name, separator, value = line.partition(':')
            if not separator or not re.fullmatch('[A-Za-z][A-Za-z0-9-]*', name) or name.lower() in fields:
                raise ValueError('invalid/duplicate package metadata field')
            key = name.lower(); fields[key] = value.strip()


def packages(raw):
    result = []
    for fields in stanzas(raw):
        # A never-installed entry has no corresponding package payload/version.
        if fields.get('status', '').split()[-1:] == ['not-installed']:
            continue
        name, version, arch = (fields.get(x, '') for x in ('package', 'version', 'architecture'))
        if (not re.fullmatch(NAME, name) or not re.fullmatch(VERSION, version)
                or not re.fullmatch('[a-z0-9][a-z0-9-]*', arch)):
            raise ValueError('missing/invalid binary package identity in layer')
        source = SOURCE.fullmatch(fields.get('source', name))
        if not source:
            raise ValueError('invalid source package identity in layer')
        # Debian Policy 5.6.1: omitted Source/name/version means exactly the
        # binary value, not a heuristic fallback from scanner metadata.
        result.append({'source': source[1], 'source_version': source[2] or version,
                       'name': name, 'version': version, 'arch': arch, 'relationship': 'source'})
        for field in ('built-using', 'static-built-using'):
            if field not in fields:
                continue
            for item in fields[field].replace('\n', ' ').split(','):
                match = re.fullmatch(rf'({NAME})\s+\(=\s+({VERSION})\)', item.strip())
                if not match:
                    raise ValueError('non-exact embedded source relationship in layer')
                result.append({'source': match[1], 'source_version': match[2],
                               'name': name, 'version': version, 'arch': arch, 'relationship': field})
    return result


def inspect_layer(stream):
    result = {'metadata': [], 'unresolved': []}
    with tarfile.open(fileobj=stream, mode='r|*') as layer:
        for member in layer:
            name = safe_name(member.name)
            if name not in ('var/lib/dpkg/status', 'var/lib/dpkg/status-old') and not re.fullmatch(r'var/lib/dpkg/status\.d/[^/]+', name):
                continue
            if member.isdir():
                continue
            if not member.isfile():
                result['unresolved'].append({'path': name, 'reason': 'nonregular-package-metadata-not-followed'})
                continue
            if member.size > MAX_STATUS:
                raise ValueError('package metadata exceeds size limit')
            raw = layer.extractfile(member).read(MAX_STATUS + 1)
            if len(raw) != member.size or len(raw) > MAX_STATUS:
                raise ValueError('package metadata size mismatch')
            result['metadata'].append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                       'packages': packages(raw)})
    return result


def image_layers(path, proof, cache):
    result = []
    with tarfile.open(path) as archive:
        saved = json.load(archive.extractfile('manifest.json'))[0]
        for name, blob, diff in zip(saved['Layers'], proof['layer_digests'], proof['diff_ids'], strict=True):
            # Key the cache by VERIFIED stored bytes, not only a diff ID claimed
            # in an OCI config. Configs with identical claims cannot alias data.
            if blob not in cache:
                cache[blob] = inspect_layer(archive.extractfile(name))
            result.append({'layer_digest': blob, 'diff_id': diff, **cache[blob]})
    return result
