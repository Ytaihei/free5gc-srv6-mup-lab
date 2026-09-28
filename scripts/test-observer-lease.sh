#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/.." && pwd)
source "$root_dir/scripts/lab-lib.sh"
require_command jq
require_command ssh

if [[ ${MUP_ENABLE:-0} != 1 ]]; then
  echo 'This test deliberately stops the PFCP observer. Enable it explicitly:' >&2
  echo '  MUP_ENABLE=1 make test-lease' >&2
  exit 2
fi

cleanup() {
  lab_ssh "$LAB_CORE" 'sudo systemctl start pfcp-observer' >/dev/null 2>&1 || true
  lab_ssh "$LAB_RAN" 'sudo systemctl start ueransim-ue' >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for_status() {
  local jq_filter=$1
  local description=$2
  local attempts=${3:-30}
  local status
  for _ in $(seq 1 "$attempts"); do
    status=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl status' || true)
    if jq -e "$jq_filter" <<<"$status" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for ${description}; last status: ${status:-unavailable}" >&2
  return 1
}

ue_address=$(wait_for_ue)
session_key=$(lab_ssh "$LAB_MUPC" '/usr/local/bin/mupctl sessions' |
  jq -r --arg ue "$ue_address" '.sessions[] | select(.session.ue_ipv4 == $ue) | .session.key' |
  head -1)
[[ -n $session_key ]] || { echo "No observed PFCP session for $ue_address" >&2; exit 1; }
lab_ssh_argv "$LAB_MUPC" /usr/local/bin/mupctl resume "$session_key" >/dev/null
wait_for_status '.observer_lease_valid == true and .advertised_routes == 2' 'initial advertised routes'

lab_ssh "$LAB_CORE" 'sudo systemctl stop pfcp-observer'
wait_for_status '.observer_lease_valid == false and .advertised_routes == 0' 'lease expiry and route withdrawal' 25
lab_ssh "$LAB_RAN" "ping -I uesimtun0 -c 4 -W 2 '$LAB_DN_IPV4'"

# The observer intentionally keeps no durable PFCP state. Restarting the UE
# creates a fresh PFCP session, which proves recovery comes from live N4 state
# rather than stale controller data.
lab_ssh "$LAB_CORE" 'sudo systemctl start pfcp-observer'
lab_ssh "$LAB_RAN" 'sudo systemctl restart ueransim-ue'
ue_address=$(wait_for_ue)
wait_for_status '.observer_lease_valid == true and .observed_sessions >= 1 and .advertised_routes == 2' 'observer and MUP route recovery' 45
lab_ssh "$LAB_RAN" "ping -I uesimtun0 -c 4 -W 2 '$LAB_DN_IPV4'"

cleanup
trap - EXIT
echo "Observer lease passed: expiry withdrew MUP routes, fallback stayed live, and fresh PFCP state restored the bypass for UE ${ue_address}."
