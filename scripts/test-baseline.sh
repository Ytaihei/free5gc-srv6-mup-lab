#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/.." && pwd)
source "$root_dir/scripts/lab-lib.sh"
require_command jq
require_command ssh

for host in "${LAB_HOSTS[@]}"; do
  lab_ssh "$host" true
done

lab_ssh "$LAB_CORE" 'sudo systemctl is-active --quiet free5gc-lab pfcp-observer'
for host in "$LAB_TPE" "$LAB_NPE"; do
  lab_ssh "$host" 'sudo systemctl is-active --quiet vinbero vinbero-bootstrap'
  lab_ssh "$host" 'sudo systemctl is-enabled vpp.service 2>/dev/null | grep -q disabled || ! dpkg-query -W vpp >/dev/null 2>&1'
done
lab_ssh "$LAB_MUPC" 'sudo systemctl is-active --quiet mup-controller'

ue_address=$(wait_for_ue)
session_key=
for attempt in $(seq 1 30); do
  sessions=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl sessions' || true)
  session_key=$(jq -r --arg ue "$ue_address" '.sessions[]? | select(.session.ue_ipv4 == $ue) | .session.key' <<<"$sessions" | head -1)
  [[ -n $session_key ]] && break
  sleep 1
done
[[ -n $session_key ]] || { echo "PFCP observer did not publish UE $ue_address" >&2; exit 1; }

cleanup() {
  lab_ssh_argv "$LAB_MUPC" /usr/local/bin/mupctl resume "$session_key" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Suppression withdraws T1/T2 but leaves PFCP and the ordinary UPF session
# untouched. Traffic must therefore use gNB -> MUP PE (N3/Interwork side) -> UPF -> MUP PE (N6/Direct side) -> DN.
lab_ssh_argv "$LAB_MUPC" /usr/local/bin/mupctl suppress "$session_key" >/dev/null
for attempt in $(seq 1 15); do
  advertised=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl sessions' | jq -r --arg key "$session_key" '.sessions[] | select(.session.key == $key) | .advertised')
  [[ $advertised == false ]] && break
  [[ $attempt != 15 ]] || { echo "session routes were not withdrawn" >&2; exit 1; }
  sleep 1
done

lab_ssh "$LAB_RAN" "ping -I uesimtun0 -c 4 -W 2 '$LAB_DN_IPV4'"
lab_ssh "$LAB_RAN" "curl --fail --max-time 10 --interface uesimtun0 'http://$LAB_DN_IPV4/'" | grep -q 'free5GC SRv6 MUP lab'

status=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl status')
jq -e '.observer_lease_valid == true and .observed_sessions >= 1' <<<"$status" >/dev/null

cleanup
trap - EXIT
echo "Fallback passed: UE ${ue_address} reached the DN through the conventional UPF path."
