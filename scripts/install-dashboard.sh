#!/usr/bin/env bash
set -euo pipefail
export GOTOOLCHAIN=local

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
lab_config=("$repo_dir/scripts/lab-config.py")
go_bin=${GO:-go}
go_version=$(python3 "$repo_dir/scripts/lock-value.py" toolchains.go.version)
locked_go=${XDG_CACHE_HOME:-${HOME}/.cache}/srv6-mup/go/${go_version}/bin/go
if [[ -n ${GO:-} ]]; then
  go_bin=$GO
elif [[ -x $locked_go ]]; then
  go_bin=$locked_go
elif [[ -x ${HOME}/.cache/srv6-mup/go/bin/go ]]; then
  go_bin=${HOME}/.cache/srv6-mup/go/bin/go
fi

install -d "${HOME}/.local/bin" "${HOME}/.config/systemd/user" "${HOME}/.config/srv6-mup"
CGO_ENABLED=0 "${go_bin}" build -trimpath -o "${HOME}/.local/bin/srv6-mup-dashboard" "${repo_dir}/cmd/mup-dashboard"
install -m 0644 "${repo_dir}/configs/systemd/srv6-mup-dashboard.service" "${HOME}/.config/systemd/user/srv6-mup-dashboard.service"
tailnet_port=0
if [[ $("${lab_config[@]}" get host.dashboard.tailscale.enabled) == true ]]; then
  if ip -4 address show "$("${lab_config[@]}" get host.dashboard.tailscale.interface)" 2>/dev/null | grep -q 'inet '; then
    tailnet_port=$("${lab_config[@]}" get host.dashboard.tailscale.port)
  else
    echo 'Tailscale IPv4 interface unavailable: installing loopback-only dashboard. Rerun after connecting Tailscale.' >&2
  fi
fi
printf '%s\n' \
  "MUP_DASHBOARD_LISTEN=$("${lab_config[@]}" get host.dashboard.listen)" \
  "MUP_DASHBOARD_INTERVAL=$("${lab_config[@]}" get host.dashboard.interval)" \
  "MUP_TAILNET_INTERFACE=$("${lab_config[@]}" get host.dashboard.tailscale.interface)" \
  "MUP_TAILNET_PORT=${tailnet_port}" \
  "MUP_CONTROLLER=http://$("${lab_config[@]}" get nodes.mupc.management_ipv4):9443" \
  "MUP_SSH_USER=$("${lab_config[@]}" get host.ansible_user)" \
  "MUP_SSH_KEY=$("${lab_config[@]}" get host.ssh_private_key --expand-path)" \
  "MUP_LAB_CORE_NAME=$("${lab_config[@]}" get nodes.core.name)" \
  "MUP_LAB_RAN_NAME=$("${lab_config[@]}" get nodes.ran.name)" \
  "MUP_LAB_TPE_NAME=$("${lab_config[@]}" get nodes.tpe.name)" \
  "MUP_LAB_NPE_NAME=$("${lab_config[@]}" get nodes.npe.name)" \
  "MUP_LAB_MUPC_NAME=$("${lab_config[@]}" get nodes.mupc.name)" \
  "MUP_LAB_DN_NAME=$("${lab_config[@]}" get nodes.dn.name)" \
  "MUP_LAB_CORE=$("${lab_config[@]}" get nodes.core.management_ipv4)" \
  "MUP_LAB_RAN=$("${lab_config[@]}" get nodes.ran.management_ipv4)" \
  "MUP_LAB_TPE=$("${lab_config[@]}" get nodes.tpe.management_ipv4)" \
  "MUP_LAB_NPE=$("${lab_config[@]}" get nodes.npe.management_ipv4)" \
  "MUP_LAB_MUPC=$("${lab_config[@]}" get nodes.mupc.management_ipv4)" \
  "MUP_LAB_DN=$("${lab_config[@]}" get nodes.dn.management_ipv4)" \
  "MUP_DN_TARGET=$("${lab_config[@]}" get networks.n6.dn_ipv4)" \
  >"${HOME}/.config/srv6-mup/dashboard.env"
chmod 0600 "${HOME}/.config/srv6-mup/dashboard.env"
systemctl --user daemon-reload
systemctl --user enable srv6-mup-dashboard.service
systemctl --user restart srv6-mup-dashboard.service
