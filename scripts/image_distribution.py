"""Image-specific license classification, not legal or publication approval."""
from collections import Counter


def license_index(policy):
    if policy.get('schema_version') != 1 or policy.get('scope') != 'container-images-only':
        raise ValueError('invalid image distribution policy')
    index = {}
    for name, group in policy['license_groups'].items():
        if not group.get('requires'):
            raise ValueError('license group must specify review materials')
        for identifier in group['licenses']:
            if identifier in index:
                raise ValueError('duplicate license classification')
            index[identifier] = {'group': name, 'requires': group['requires']}
    return index


def classify(report, policy):
    index = license_index(policy)
    supplements = {(x['name'], x['version']): x['licenses'] for x in policy.get('license_metadata', [])}
    packages, files, counts = [], [], Counter()
    for result in report.get('Results') or []:
        # Full-license scanning also reports headers/files with no package row.
        # Keep those obligations visible rather than treating package inventory
        # as complete coverage of vendored/native/embedded code.
        for item in result.get('Licenses') or []:
            label = item.get('Name')
            rule = index.get(label, {'group': 'unreviewed', 'requires': ['exact-license-text-review']})
            files.append({'target': result.get('Target'), 'file_path': item.get('FilePath'),
                          'package_name': item.get('PkgName'), 'license': label,
                          'group': rule['group'], 'requires': rule['requires'],
                          'materials_status': 'pending-review'})
        for package in result.get('Packages') or []:
            labels = package.get('Licenses') or supplements.get((package.get('Name'), package.get('Version'))) or []
            groups = sorted({index.get(x, {'group': 'unreviewed'})['group'] for x in labels} or {'unknown'})
            requires = sorted({value for label in labels for value in index.get(label, {'requires': ['exact-license-text-review']})['requires']})
            if not labels:
                requires = ['exact-version-license-identification']
            counts.update(groups)
            packages.append({'target': result.get('Target'), 'type': result.get('Type'),
                             'name': package.get('Name'), 'version': package.get('Version'),
                             'source_name': package.get('SrcName'), 'source_version': package.get('SrcVersion'),
                             'licenses': labels, 'groups': groups, 'requires': requires,
                             'materials_status': 'pending-review'})
    return {'packages': packages, 'file_licenses': files, 'groups': dict(counts),
            'artifact_requirements': policy['required_artifact_materials'],
            'distribution_approved': False}


def key_origin(path, hashes, policy):
    """Match full file hashes, not a filename, regex snippet, or key contents."""
    matches = [x for x in policy['keys'] if x['path'] == path and hashes and set(hashes) == {x['sha256']}]
    if len(matches) != 1:
        return {'classification': 'unverified', 'scanner_exempted': False}
    item = matches[0]
    return {'classification': item['classification'], 'sha256': item['sha256'],
            'source': policy['upstreams'][item['upstream']] + item['file'], 'scanner_exempted': False}
