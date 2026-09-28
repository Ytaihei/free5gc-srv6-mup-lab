"""Render the single-PLMN, single-slice lab from pinned upstream YAML.

Never transform an already modified file: every run uses `git show <lock>:...`.
Keep leading-zero identifiers as strings instead of treating SD/TAC as octal.
"""
from copy import deepcopy
import json
import re

import yaml


class IdentifierSafeLoader(yaml.SafeLoader):
    pass


IdentifierSafeLoader.yaml_implicit_resolvers = {
    key: [(tag, rule) for tag, rule in values if tag != 'tag:yaml.org,2002:int']
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
IdentifierSafeLoader.add_implicit_resolver(
    'tag:yaml.org,2002:int', re.compile(r'^[-+]?(?:0|[1-9][0-9]*)$'), list('-+0123456789')
)


def render_free5gc(source, name, lab):
    # Strip Ansible's unsafe/tagged string subclasses before PyYAML dumping.
    lab = json.loads(json.dumps(lab))
    model = yaml.load(source, Loader=IdentifierSafeLoader)
    sub, nets = lab['subscriber'], lab['networks']
    plmn = {'mcc': sub['mcc'], 'mnc': sub['mnc']}
    slice_id = {'sst': sub['sst'], 'sd': sub['sd']}
    dnn, pool = sub['dnn'], nets['ue']['pool']
    cfg = model.get('configuration', model)
    if name == 'amf':
        cfg['ngapIpList'] = [nets['n2']['amf_ipv4']]
        cfg['servedGuamiList'] = [{'plmnId': plmn, 'amfId': 'cafe00'}]
        cfg['supportTaiList'] = [{'plmnId': plmn, 'tac': '000001'}]
        cfg['plmnSupportList'] = [{'plmnId': plmn, 'snssaiList': [slice_id]}]
        cfg['supportDnnList'] = [dnn]
    elif name == 'smf':
        info = deepcopy(cfg['snssaiInfos'][0])
        info['sNssai'] = slice_id
        info['dnnInfos'] = [dict(info['dnnInfos'][0], dnn=dnn)]
        cfg['snssaiInfos'] = [info]
        cfg['plmnList'] = [plmn]
        upf = cfg['userplaneInformation']['upNodes']['UPF']
        upf['sNssaiUpfInfos'] = [{'sNssai': slice_id, 'dnnUpfInfoList': [
            {'dnn': dnn, 'pools': [{'cidr': pool}]}]}]
        for interface in upf['interfaces']:
            if interface['interfaceType'] == 'N3':
                interface['endpoints'] = [nets['n3_core']['upf_ipv4']]
                interface['networkInstances'] = [dnn]
    elif name == 'upf':
        for interface in cfg['gtpu']['ifList']:
            if interface['type'] == 'N3':
                interface['addr'] = nets['n3_core']['upf_ipv4']
        cfg['dnnList'] = [{'dnn': dnn, 'cidr': pool}]
    elif name == 'nrf':
        cfg['DefaultPlmnId'] = plmn
    elif name == 'ausf':
        cfg['plmnSupportList'] = [plmn]
    elif name == 'nssf':
        cfg['supportedPlmnList'] = [plmn]
        cfg['supportedNssaiInPlmnList'] = [{'plmnId': plmn, 'supportedSnssaiList': [slice_id]}]
        cfg['nsiList'] = [{'snssai': slice_id, 'nsiInformationList': [{
            'nrfId': cfg['nrfUri'] + '/nnrf-nfm/v1/nf-instances', 'nsiId': '22'}]}]
        cfg['amfSetList'] = []
        cfg['amfList'] = []
        cfg['taList'] = [{'tai': {'plmnId': plmn, 'tac': 1},
                          'accessType': '3GPP_ACCESS', 'supportedSnssaiList': [slice_id]}]
        cfg['mappingListFromPlmn'] = []
    else:
        raise ValueError(f'unsupported free5GC component: {name}')
    # Avoid YAML aliases: the upstream NF parsers need independent values.
    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True
    return yaml.dump(model, Dumper=Dumper, sort_keys=False)


class FilterModule:
    def filters(self):
        return {'lab_free5gc': render_free5gc,
                'lab_from_json': lambda value: json.loads(value) if isinstance(value, str) else value}
