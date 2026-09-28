#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
lab_config=("$root_dir/scripts/lab-config.py")
uri=$("${lab_config[@]}" get host.libvirt_uri)
sr_network=$("${lab_config[@]}" get networks.sr_underlay.name)
sr_bridge=$("${lab_config[@]}" get networks.sr_underlay.bridge)
sr_mtu=$("${lab_config[@]}" get networks.sr_underlay.mtu)
tpe_name=$("${lab_config[@]}" get nodes.tpe.name)
npe_name=$("${lab_config[@]}" get nodes.npe.name)
temporary_xmls=()
cleanup() {
  if ((${#temporary_xmls[@]})); then
    rm -f -- "${temporary_xmls[@]}"
  fi
}
trap cleanup EXIT

for template in "$root_dir"/infra/libvirt/networks/*.xml.j2; do
  restart=false
  rendered_xml=$(mktemp)
  temporary_xmls+=("$rendered_xml")
  "${lab_config[@]}" render "$template" >"$rendered_xml"
  name=$(sed -n 's:.*<name>\([^<]*\)</name>.*:\1:p' "$rendered_xml" | head -1)
  [[ -n $name ]] || { echo "Rendered network has no name: $template" >&2; exit 1; }
  define_xml=$rendered_xml
  if virsh -c "$uri" net-info "$name" >/dev/null 2>&1; then
    if [[ $(virsh -c "$uri" net-info "$name" | awk '/Active:/ {print $2}') == yes ]]; then
      current_xml=$(mktemp)
      temporary_xmls+=("$current_xml")
      virsh -c "$uri" net-dumpxml "$name" >"$current_xml"
      python3 "$root_dir/scripts/network-definition.py" "$current_xml" "$rendered_xml"
    fi
    # libvirt requires an existing persistent network's UUID when redefining
    # it. Preserve that UUID while taking every other field from source control.
    uuid=$(virsh -c "$uri" net-uuid "$name")
    define_xml=$(mktemp)
    temporary_xmls+=("$define_xml")
    sed "s#<name>${name}</name>#<name>${name}</name><uuid>${uuid}</uuid>#" "$rendered_xml" >"$define_xml"
  fi
  if [[ $name == "$sr_network" ]] && virsh -c "$uri" net-info "$name" >/dev/null 2>&1; then
    if [[ $(virsh -c "$uri" net-info "$name" | awk '/Active:/ {print $2}') == yes ]]; then
      current_mtu=$(ip -o link show "$sr_bridge" 2>/dev/null | sed -n 's/.* mtu \([0-9]*\) .*/\1/p')
      [[ $current_mtu == "$sr_mtu" ]] || restart=true
    fi
  fi
  virsh -c "$uri" net-define "$define_xml" >/dev/null
  if $restart; then
    if virsh -c "$uri" list --name | grep -Fx -e "$tpe_name" -e "$npe_name" >/dev/null; then
      echo "$name: persistent MTU updated; stop PE guests and rerun networks to activate MTU $sr_mtu" >&2
      restart=false
    else
      virsh -c "$uri" net-destroy "$name" >/dev/null
    fi
  fi
  virsh -c "$uri" net-autostart "$name" >/dev/null
  if [[ $(virsh -c "$uri" net-info "$name" | awk '/Active:/ {print $2}') != yes ]]; then
    virsh -c "$uri" net-start "$name" >/dev/null
  fi
  echo "$name: active"
done
