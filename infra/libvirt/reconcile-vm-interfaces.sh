#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
lab_config=("$root_dir/scripts/lab-config.py")
uri=$("${lab_config[@]}" get host.libvirt_uri)
tpe=$("${lab_config[@]}" get nodes.tpe.name)
npe=$("${lab_config[@]}" get nodes.npe.name)
n3_core=$("${lab_config[@]}" get networks.n3_core.name)
n6=$("${lab_config[@]}" get networks.n6.name)
tpe_n3_core_mac=$("${lab_config[@]}" get nodes.tpe.n3_core_mac)
npe_n6_mac=$("${lab_config[@]}" get nodes.npe.n6_mac)

domain_running() {
  [[ $(virsh -c "$uri" domstate "$1" 2>/dev/null) == running ]]
}

network_mac() {
  virsh -c "$uri" domiflist "$1" --inactive | awk -v network="$2" '$3 == network {print $5; exit}'
}

ensure_interface() {
  local domain=$1 network=$2 mac=$3
  if [[ -n $(network_mac "$domain" "$network") ]]; then
    echo "$domain: $network already attached"
    return
  fi
  local flags=(--config)
  if domain_running "$domain"; then flags+=(--live); fi
  virsh -c "$uri" attach-interface \
    --domain "$domain" --type network --source "$network" \
    --model virtio --mac "$mac" "${flags[@]}" >/dev/null
  echo "$domain: attached $network"
}

remove_interface() {
  local domain=$1 network=$2 mac
  mac=$(network_mac "$domain" "$network")
  if [[ -z $mac ]]; then
    echo "$domain: $network already absent"
    return
  fi
  local flags=(--config)
  if domain_running "$domain"; then flags+=(--live); fi
  virsh -c "$uri" detach-interface \
    --domain "$domain" --type network --mac "$mac" "${flags[@]}" >/dev/null
  echo "$domain: detached $network ($mac)"
}

for domain in "$tpe" "$npe"; do
  virsh -c "$uri" dominfo "$domain" >/dev/null 2>&1 || {
    echo "$domain is not defined; create-vms.sh must run first" >&2
    exit 1
  }
done

ensure_interface "$tpe" "$n3_core" "$tpe_n3_core_mac"
remove_interface "$npe" "$n3_core"
ensure_interface "$npe" "$n6" "$npe_n6_mac"
