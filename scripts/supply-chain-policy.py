#!/usr/bin/env python3
"""Fail closed on high/critical CVEs, unreviewed licenses or empty scans."""
import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def license_allowed(license_name, name, version, policy):
    if license_name in policy['allowed_package_licenses']:
        return True
    return any(item['name'] == name and item['version'] == version and
               license_name in item['licenses'] and item.get('reason')
               for item in policy.get('license_exceptions', []))


def evaluate(report, policy, license_index=None):
    license_index = license_index or {}
    errors = []
    results = report.get('Results') or []
    if report.get('SchemaVersion') != 2 or not results or not any(r.get('Packages') for r in results):
        return ['scan did not produce a supported nonempty package inventory']
    for result in results:
        target = result.get('Target', '?')
        for vulnerability in result.get('Vulnerabilities') or []:
            if vulnerability.get('Severity') in policy['fail_severities']:
                errors.append(f"{target}: {vulnerability['Severity']} {vulnerability['VulnerabilityID']} "
                              f"{vulnerability['PkgName']}@{vulnerability['InstalledVersion']} "
                              f"fixed={vulnerability.get('FixedVersion') or 'unavailable'}")
        for license_item in result.get('Licenses') or []:
            if not license_allowed(license_item.get('Name'), license_item.get('PkgName'), license_item.get('PkgVersion'), policy):
                errors.append(f"{target}: unreviewed license {license_item.get('Name')} "
                              f"({license_item.get('PkgName', '?')})")
        # Missing license metadata must also be visible, not silently allowed.
        for package in result.get('Packages') or []:
            licenses = package.get('Licenses') or license_index.get((package.get('Name'), package.get('Version')))
            if not licenses:
                errors.append(f"{target}: license unknown for {package.get('Name', '?')}")
            for license_name in licenses or []:
                if not license_allowed(license_name, package.get('Name'), package.get('Version'), policy):
                    errors.append(f"{target}: unreviewed license {license_name} ({package.get('Name', '?')})")
    return sorted(set(errors))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--enriched-dir', type=Path)
    args = parser.parse_args()
    policy = yaml.safe_load((ROOT / 'config/supply-chain-policy.yml').read_text())
    reports = [(path, json.loads(path.read_text())) for path in args.reports]
    # Binary metadata has no license texts: join to the SAME module version
    # detected from go.mod, never to an unversioned package-name allowlist.
    license_index = {(item['name'], item['version']): item['licenses']
                     for item in policy.get('license_metadata', [])}
    for _, report in reports:
        for result in report.get('Results') or []:
            for package in result.get('Packages') or []:
                if package.get('Licenses'):
                    license_index[(package['Name'], package.get('Version'))] = package['Licenses']
                if package['Name'] == 'github.com/Ytaihei/free5gc-srv6-mup-lab':
                    if 'Apache License' not in (ROOT / 'LICENSE').read_text():
                        raise ValueError('Original project license changed; review policy')
                    license_index[(package['Name'], package.get('Version'))] = ['Apache-2.0']
    failures = []
    for path, report in reports:
        errors = evaluate(report, policy, license_index)
        failures.extend(errors)
        print(f'{path}: {len(errors)} policy finding(s)')
        for error in errors:
            print(f'  FAIL {error}')
        if args.enriched_dir:
            args.enriched_dir.mkdir(parents=True, exist_ok=True)
            for result in report.get('Results') or []:
                for package in result.get('Packages') or []:
                    if not package.get('Licenses'):
                        package['Licenses'] = license_index.get((package['Name'], package.get('Version')), [])
            (args.enriched_dir / (path.stem + '.licensed.json')).write_text(json.dumps(report, indent=2) + '\n')
    raise SystemExit(bool(failures))


if __name__ == '__main__':
    main()
