#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/.." && pwd)
source "$root_dir/scripts/lab-lib.sh"

require_command curl
require_command jq
require_command ssh

if [[ ${ONE_CALL_ENABLE:-0} != 1 ]]; then
  echo 'This test de-registers and re-registers the lab UE.' >&2
  echo 'Run it explicitly while watching the dashboard:' >&2
  echo '  ONE_CALL_ENABLE=1 make test-one-call' >&2
  exit 2
fi

observe_seconds=${OBSERVE_SECONDS:-8}
if ! [[ $observe_seconds =~ ^[0-9]+$ ]]; then
  echo 'OBSERVE_SECONDS must be a non-negative integer.' >&2
  exit 2
fi

ran=$LAB_RAN
tpe=$LAB_TPE
npe=$LAB_NPE
mupc=$LAB_MUPC
dn=$LAB_DN_IPV4
ue_node=$LAB_UE_NODE
dashboard_url=${DASHBOARD_URL:-http://$LAB_DASHBOARD_LISTEN}
dashboard_url=${dashboard_url%/}
test_started=$(date +%s)

stage() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$1"
}

dashboard_snapshot() {
  local snapshot
  snapshot=$(curl --fail --silent --max-time 3 "$dashboard_url/api/state" 2>/dev/null || true)
  if [[ -z $snapshot ]]; then
    echo 'Dashboard API is unavailable; the 1call test will continue.'
    return
  fi
  jq -r '
    "dashboard: overall=\(.overall) nodes=\([.nodes[] | select(.healthy)] | length)/\(.nodes | length)" +
    " observed=\(.controller.observed_sessions) selected=\(.controller.selected_sessions)" +
    " routes=\(.controller.advertised_routes) mup=\(.paths.mup_active)"' <<<"$snapshot"
}

observe() {
  local expectation=$1
  echo "Dashboard: ${expectation}"
  if (( observe_seconds > 0 )); then
    echo "Observation pause: ${observe_seconds}s (${dashboard_url})"
    sleep "$observe_seconds"
  fi
  dashboard_snapshot
}

wait_for_controller() {
  local jq_filter=$1
  local description=$2
  local attempts=${3:-40}
  local status=
  for _ in $(seq 1 "$attempts"); do
    status=$(lab_ssh "$mupc" '/usr/local/bin/mupctl status' 2>/dev/null || true)
    if jq -e "$jq_filter" <<<"$status" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for ${description}; last controller status:" >&2
  jq . <<<"${status:-null}" >&2 || printf '%s\n' "$status" >&2
  return 1
}

wait_for_log() {
  local pattern=$1
  local description=$2
  local attempts=${3:-30}
  for _ in $(seq 1 "$attempts"); do
    if lab_ssh "$ran" "sudo journalctl -u ueransim-ue --since '@${ue_start_epoch}' --no-pager -o cat | grep -Fq '$pattern'"; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for ${description}." >&2
  lab_ssh "$ran" "sudo journalctl -u ueransim-ue --since '@${ue_start_epoch}' -n 120 --no-pager" >&2 || true
  return 1
}

redirect_packets() {
  local host=$1
  lab_ssh "$host" 'sudo env VINBERO_SERVER=http://127.0.0.1:8080 /usr/local/bin/vinbero --json stats show' |
    jq -r '[.[] | select(.name == "REDIRECT") | (.packets // 0)][0] // 0'
}

cleanup() {
  echo 'Recovery: ensuring the UERANSIM UE service is running.' >&2
  lab_ssh "$ran" 'sudo systemctl start ueransim-ue' >/dev/null 2>&1 || true
}
trap cleanup EXIT

stage 'Preflight'
for host in "${LAB_HOSTS[@]}"; do
  lab_ssh "$host" true
done
lab_ssh "$LAB_CORE" 'sudo systemctl is-active --quiet free5gc-lab pfcp-observer'
lab_ssh "$ran" 'sudo systemctl is-active --quiet ueransim-gnb'
lab_ssh "$tpe" 'sudo systemctl is-active --quiet vinbero vinbero-bootstrap'
lab_ssh "$npe" 'sudo systemctl is-active --quiet vinbero vinbero-bootstrap'
lab_ssh "$mupc" 'sudo systemctl is-active --quiet mup-controller'
wait_for_controller '.observer_lease_valid == true and .bgp_state == "2/2 established"' 'healthy MUP control plane'
dashboard_snapshot

old_session=$(lab_ssh "$mupc" '/usr/local/bin/mupctl sessions' || true)
old_ue=$(jq -r '.sessions[0].session.ue_ipv4 // "none"' <<<"$old_session")
old_key=$(jq -r '.sessions[0].session.key // "none"' <<<"$old_session")
echo "Current UE=${old_ue}, session=${old_key}"

stage 'UE de-registration and session withdrawal'
if lab_ssh "$ran" 'systemctl is-active --quiet ueransim-ue'; then
  lab_ssh "$ran" "cd /opt/UERANSIM && ./build/nr-cli '$ue_node' --exec 'deregister switch-off'" || true
fi
lab_ssh "$ran" 'sudo systemctl stop ueransim-ue'

for attempt in $(seq 1 20); do
  if ! lab_ssh "$ran" 'ip link show uesimtun0' >/dev/null 2>&1; then
    break
  fi
  [[ $attempt != 20 ]] || { echo 'uesimtun0 did not disappear.' >&2; exit 1; }
  sleep 1
done
wait_for_controller '.observer_lease_valid == true and .observed_sessions == 0 and .selected_sessions == 0 and .advertised_routes == 0' 'PFCP deletion and T1/T2 withdrawal'
observe 'RAN is degraded; PFCP sessions=0; MUP routes=0; UPF fallback is highlighted.'

stage 'UE Initial Registration'
ue_start_epoch=$(lab_ssh "$ran" 'date +%s')
lab_ssh "$ran" 'sudo systemctl start ueransim-ue'
wait_for_log 'Initial Registration is successful' 'successful Initial Registration'
echo 'NAS: Initial Registration is successful.'

stage 'PDU Session establishment'
wait_for_log 'PDU Session establishment is successful PSI[1]' 'PDU Session establishment'
ue_address=$(wait_for_ue)
echo "PDU session: uesimtun0=${ue_address}"

session_json=
session_key=
for attempt in $(seq 1 45); do
  session_json=$(lab_ssh "$mupc" '/usr/local/bin/mupctl sessions' || true)
  session_key=$(jq -r --arg ue "$ue_address" '.sessions[]? | select(.session.ue_ipv4 == $ue and .advertised == true) | .session.key' <<<"$session_json" | head -1)
  [[ -n $session_key ]] && break
  [[ $attempt != 45 ]] || { echo "PFCP-derived T1/T2 were not advertised for ${ue_address}." >&2; exit 1; }
  sleep 1
done
wait_for_controller '.observer_lease_valid == true and .observed_sessions == 1 and .selected_sessions == 1 and .advertised_routes == 2' 'PFCP session selection and T1/T2 advertisement'

for attempt in $(seq 1 30); do
  if lab_ssh "$npe" "sudo env VINBERO_SERVER=http://127.0.0.1:8080 /usr/local/bin/vinbero --json headend-v4 list | grep -Fq '$ue_address/32'" &&
     lab_ssh "$tpe" "sudo bpftool -j map dump name mup_uplink_v4_m | jq -e 'length >= 1'" >/dev/null; then
    break
  fi
  [[ $attempt != 30 ]] || { echo 'Vinbero did not program the UE headends in time.' >&2; exit 1; }
  sleep 1
done

selected_session=$(jq --arg key "$session_key" '.sessions[] | select(.session.key == $key)' <<<"$session_json")
jq '{ue: .session.ue_ipv4, dnn: .session.dnn, uplink_f_teid: {address: .session.upf_gtp_ipv4, teid: .session.uplink_teid}, downlink_f_teid: {address: .session.ran_gtp_ipv4, teid: .session.downlink_teid}, qfi: .session.qfi, selected, advertised, reason}' <<<"$selected_session"
observe "RAN is healthy; UE=${ue_address}; one PFCP session and the T1/T2 pair are visible; MUP bypass is active."

stage 'End-to-end user-plane traffic'
tpe_before=$(redirect_packets "$tpe")
npe_before=$(redirect_packets "$npe")
echo "XDP REDIRECT before: MUP PE (N3/Interwork side)=${tpe_before}, MUP PE (N6/Direct side)=${npe_before}"

lab_ssh "$ran" "ping -I uesimtun0 -c 8 -i 0.5 -W 2 '$dn'"
http_body=$(lab_ssh "$ran" "curl --fail --silent --show-error --max-time 10 --interface uesimtun0 http://'$dn'/")
grep -q 'free5GC SRv6 MUP lab' <<<"$http_body"
echo 'HTTP: expected DN response received.'

tpe_after=$(redirect_packets "$tpe")
npe_after=$(redirect_packets "$npe")
(( tpe_after > tpe_before )) || { echo 'MUP PE (N3/Interwork side) REDIRECT counter did not increase.' >&2; exit 1; }
(( npe_after > npe_before )) || { echo 'MUP PE (N6/Direct side) REDIRECT counter did not increase.' >&2; exit 1; }
echo "XDP REDIRECT after:  MUP PE (N3/Interwork side)=${tpe_after} (+$((tpe_after - tpe_before))), MUP PE (N6/Direct side)=${npe_after} (+$((npe_after - npe_before)))"
observe 'MUP PE (N3/Interwork side)/MUP PE (N6/Direct side) REDIRECT packet counters have increased while MUP remains active.'

stage '1call passed'
echo "UE ${ue_address}: Registration -> PDU Session -> PFCP observation -> T1/T2 -> ICMP/HTTP succeeded in $(($(date +%s) - test_started))s."
echo "Dashboard: ${dashboard_url}"

trap - EXIT
