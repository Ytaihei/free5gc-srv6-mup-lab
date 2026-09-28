#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/.." && pwd)
source "$root_dir/scripts/lab-lib.sh"
if [[ ${NETWORK_RECOVERY_ENABLE:-0} != 1 ]]; then
  echo 'This test reapplies/restarts PE networking and re-registers the UE.' >&2
  echo 'Run explicitly: NETWORK_RECOVERY_ENABLE=1 make test-network-recovery' >&2
  exit 2
fi
require_command jq
require_command ssh
tpe_locator=$("${lab_config[@]}" get networks.sr_underlay.tpe_locator)
npe_locator=$("${lab_config[@]}" get networks.sr_underlay.npe_locator)
tpe_next_hop=$("${lab_config[@]}" get networks.sr_underlay.tpe_ipv6)
npe_next_hop=$("${lab_config[@]}" get networks.sr_underlay.npe_ipv6)
ue_pool=$("${lab_config[@]}" get networks.ue.pool)
n6_subnet=$("${lab_config[@]}" get networks.n6.subnet)
upf_n6=$("${lab_config[@]}" get networks.n6.upf_ipv4)

assert_network() {
  lab_ssh "$LAB_TPE" "ip -j -6 route show '$npe_locator'" |
    jq -e --arg via "$npe_next_hop" 'any(.[]; .gateway == $via and .dev == "enp3s0")' >/dev/null || return 1
  lab_ssh "$LAB_NPE" "ip -j -6 route show '$tpe_locator'" |
    jq -e --arg via "$tpe_next_hop" 'any(.[]; .gateway == $via and .dev == "enp2s0")' >/dev/null || return 1
  lab_ssh "$LAB_NPE" 'ip -j link show enp3s0' |
    jq -e '.[0].master == "mup-dn"' >/dev/null || return 1
  lab_ssh "$LAB_NPE" "ip -j route show table 100 '$n6_subnet'" |
    jq -e 'any(.[]; .scope == "link" and .dev == "enp3s0")' >/dev/null || return 1
  lab_ssh "$LAB_NPE" "ip -j route show table 100 '$ue_pool'" |
    jq -e --arg via "$upf_n6" 'any(.[]; .gateway == $via and .dev == "enp3s0")' >/dev/null || return 1
}

assert_network
for host in "$LAB_TPE" "$LAB_NPE"; do
  lab_ssh "$host" '/lib/systemd/systemd-networkd-wait-online --timeout=10'
done
for action in 'netplan apply' 'systemctl restart systemd-networkd'; do
  echo "PE network recovery: $action"
  for host in "$LAB_TPE" "$LAB_NPE"; do
    lab_ssh "$host" "sudo $action"
  done
  ready=0
  for _ in {1..30}; do
    if assert_network >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  (( ready == 1 )) || { echo 'Persistent MUP routes/VRF did not recover.' >&2; exit 1; }
  for host in "$LAB_TPE" "$LAB_NPE"; do
    lab_ssh "$host" '/lib/systemd/systemd-networkd-wait-online --timeout=10'
  done
  # This checks Registration, PFCP, routes, ICMP/HTTP and both XDP counters.
  ONE_CALL_ENABLE=1 OBSERVE_SECONDS=2 "$root_dir/scripts/test-one-call.sh"
done

# Remove only this lab's dynamic DN cache entry, not a device or routing table.
# Do not manually ping from the MUP PE (N6/Direct side): the installed timer must recover it.
echo 'PE network recovery: DN neighbor cache eviction'
lab_ssh "$LAB_NPE" 'systemctl is-active --quiet vinbero-neighbor-refresh.timer'
lab_ssh "$LAB_NPE" 'systemctl is-enabled --quiet vinbero-neighbor-refresh.timer'
lab_ssh "$LAB_NPE" "sudo ip neigh del '$LAB_DN_IPV4' dev enp3s0"
ready=0
for _ in {1..30}; do
  if lab_ssh "$LAB_NPE" "ip -j neigh show '$LAB_DN_IPV4' dev enp3s0" |
    jq -e 'any(.[]; .lladdr != null and any(.state[]?; . == "REACHABLE" or . == "STALE" or . == "DELAY" or . == "PROBE"))' >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
(( ready == 1 )) || { echo 'Automatic DN neighbor recovery timed out.' >&2; exit 1; }
ONE_CALL_ENABLE=1 OBSERVE_SECONDS=2 "$root_dir/scripts/test-one-call.sh"
echo 'Network recovery passed: netplan apply, networkd restart and DN neighbor eviction recovered the MUP data plane.'
