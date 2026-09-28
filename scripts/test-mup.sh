#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/.." && pwd)
source "$root_dir/scripts/lab-lib.sh"
require_command jq
require_command ssh

if [[ ${MUP_ENABLE:-0} != 1 ]]; then
  echo 'Run the fallback gate first, then explicitly enable the MUP data-plane test:' >&2
  echo '  make test-baseline && MUP_ENABLE=1 make test-mup' >&2
  exit 2
fi

"$root_dir/scripts/test-baseline.sh"
ue_address=$(wait_for_ue)
session_key=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl sessions' | jq -r --arg ue "$ue_address" '.sessions[] | select(.session.ue_ipv4 == $ue) | .session.key' | head -1)
[[ -n $session_key ]] || { echo "No observed PFCP session for $ue_address" >&2; exit 1; }

cleanup() {
  lab_ssh_argv "$LAB_MUPC" /usr/local/bin/mupctl resume "$session_key" >/dev/null 2>&1 || true
}
trap cleanup EXIT
lab_ssh_argv "$LAB_MUPC" /usr/local/bin/mupctl resume "$session_key" >/dev/null

for attempt in $(seq 1 20); do
  session_json=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl sessions')
  advertised=$(jq -r --arg key "$session_key" '.sessions[] | select(.session.key == $key) | .advertised' <<<"$session_json")
  [[ $advertised == true ]] && break
  [[ $attempt != 20 ]] || { echo "T1/T2 did not become advertised" >&2; exit 1; }
  sleep 1
done

# The MUP PE (N6/Direct side) must have a per-UE downlink H.Encaps entry and the MUP PE (N3/Interwork side) must have
# both the endpoint gate and exact F-TEID entry for uplink.
lab_ssh "$LAB_NPE" "sudo env VINBERO_SERVER=http://127.0.0.1:8080 /usr/local/bin/vinbero --json headend-v4 list | grep -F '$ue_address/32'" >/dev/null
lab_ssh "$LAB_TPE" "sudo env VINBERO_SERVER=http://127.0.0.1:8080 /usr/local/bin/vinbero --json headend-v4 list | grep -F '$LAB_UPF_N3_IPV4/32'" >/dev/null
# BPF object names are limited to 15 bytes, so the kernel exposes Vinbero's
# mup_uplink_v4_map as mup_uplink_v4_m.
tpe_entries=$(lab_ssh "$LAB_TPE" "sudo bpftool -j map dump name mup_uplink_v4_m | jq length" || true)
[[ ${tpe_entries:-0} -ge 1 ]] || { echo "MUP PE (N3/Interwork side) MUP uplink F-TEID map is empty" >&2; exit 1; }

lab_ssh "$LAB_RAN" "ping -I uesimtun0 -c 6 -W 2 '$LAB_DN_IPV4'"
lab_ssh "$LAB_RAN" "curl --fail --max-time 10 --interface uesimtun0 'http://$LAB_DN_IPV4/'" | grep -q 'free5GC SRv6 MUP lab'

# Withdraw and prove the same PFCP session falls back without manual TEIDs.
lab_ssh_argv "$LAB_MUPC" /usr/local/bin/mupctl suppress "$session_key" >/dev/null
for attempt in $(seq 1 15); do
  session_json=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl sessions')
  advertised=$(jq -r --arg key "$session_key" '.sessions[] | select(.session.key == $key) | .advertised' <<<"$session_json")
  [[ $advertised == false ]] && break
  [[ $attempt != 15 ]] || { echo "T1/T2 withdrawal timed out" >&2; exit 1; }
  sleep 1
done
lab_ssh "$LAB_RAN" "ping -I uesimtun0 -c 4 -W 2 '$LAB_DN_IPV4'"

cleanup
trap - EXIT
echo "MUP passed: PFCP-derived T1/T2 installed the Vinbero bypass for UE ${ue_address}, and withdrawal restored the UPF path."
