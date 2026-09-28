#!/usr/bin/env python3
"""Compare the managed parts of libvirt network XML without runtime defaults."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def signature(document):
    root = ET.fromstring(document)
    def attributes(path):
        return [dict(sorted(element.attrib.items())) for element in root.findall(path)]
    bridge = root.find('bridge')
    forward = root.find('forward')
    return {
        'name': root.findtext('name'),
        'bridge': bridge.get('name') if bridge is not None else None,
        'forward': forward.get('mode') if forward is not None else None,
        'mtu': attributes('mtu'), 'ip': attributes('ip'),
        'dhcp_ranges': attributes('ip/dhcp/range'),
        'dhcp_hosts': sorted(attributes('ip/dhcp/host'), key=lambda item: json.dumps(item, sort_keys=True)),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('current', type=Path)
    parser.add_argument('desired', type=Path)
    args = parser.parse_args()
    if signature(args.current.read_text()) != signature(args.desired.read_text()):
        parser.exit(1, 'Active network differs from configuration; stop affected guests and network before redefinition.\n')
