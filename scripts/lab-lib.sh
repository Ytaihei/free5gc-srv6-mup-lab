#!/usr/bin/env bash
# Variables in this sourced library are consumed by the E2E test scripts.
# shellcheck disable=SC2034

lab_root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
lab_config=("$lab_root_dir/scripts/lab-config.py")
LAB_SSH_USER=$("${lab_config[@]}" get host.ansible_user)
LAB_SSH_KEY=$("${lab_config[@]}" get host.ssh_private_key --expand-path)
LAB_CORE=$("${lab_config[@]}" get nodes.core.management_ipv4)
LAB_RAN=$("${lab_config[@]}" get nodes.ran.management_ipv4)
LAB_TPE=$("${lab_config[@]}" get nodes.tpe.management_ipv4)
LAB_NPE=$("${lab_config[@]}" get nodes.npe.management_ipv4)
LAB_MUPC=$("${lab_config[@]}" get nodes.mupc.management_ipv4)
LAB_DN=$("${lab_config[@]}" get nodes.dn.management_ipv4)
LAB_DN_IPV4=$("${lab_config[@]}" get networks.n6.dn_ipv4)
LAB_UPF_N3_IPV4=$("${lab_config[@]}" get networks.n3_core.upf_ipv4)
LAB_UE_NODE=$("${lab_config[@]}" get subscriber.supi)
LAB_DASHBOARD_LISTEN=$("${lab_config[@]}" get host.dashboard.listen)
LAB_HOSTS=("$LAB_CORE" "$LAB_RAN" "$LAB_TPE" "$LAB_NPE" "$LAB_MUPC" "$LAB_DN")

lab_ssh_options=(-i "$LAB_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)

lab_ssh() {
  local host=$1
  shift
  # Each caller supplies the complete remote command and any interpolation is
  # intentional on the control host before OpenSSH sends it.
  # shellcheck disable=SC2029
  ssh "${lab_ssh_options[@]}" "${LAB_SSH_USER}@${host}" "$@"
}

lab_ssh_argv() {
  local host=$1 command
  shift
  [[ $# -gt 0 ]] || { echo 'Missing remote executable' >&2; return 2; }
  # OpenSSH sends shell text, not an argv vector. Quote every argument for
  # the Bash login shell provisioned by cloud-init, including empty strings
  # and embedded quotes/newlines. Never interpret controller data as syntax.
  printf -v command '%q ' "$@"
  lab_ssh "$host" "$command"
}

wait_for_ue() {
  local value
  for _ in {1..30}; do
    value=$(lab_ssh "$LAB_RAN" "ip -j address show uesimtun0 2>/dev/null | jq -r '.[0].addr_info[]? | select(.family == \"inet\") | .local'" 2>/dev/null || true)
    if [[ -n $value ]]; then
      printf '%s\n' "$value"
      return 0
    fi
    sleep 2
  done
  echo 'UERANSIM UE did not create uesimtun0' >&2
  lab_ssh "$LAB_RAN" 'sudo journalctl -u ueransim-gnb -u ueransim-ue -n 120 --no-pager' >&2 || true
  return 1
}

require_command() {
  command -v "$1" >/dev/null || { echo "Missing command: $1" >&2; exit 1; }
}
