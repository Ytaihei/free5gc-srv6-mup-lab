#!/usr/bin/env bash
set -euo pipefail

action=${1:-}
root_dir=$(cd "$(dirname "$0")/../.." && pwd)
source "$root_dir/scripts/host-memory.sh"
if [[ $action == start ]]; then lab_require_host_memory; fi
lab_config=("$root_dir/scripts/lab-config.py")
uri=$("${lab_config[@]}" get host.libvirt_uri)
vms=()
for role in core ran tpe npe mupc dn; do
  vms+=("$("${lab_config[@]}" get "nodes.${role}.name")")
done
case "$action" in
  start)
    for vm in "${vms[@]}"; do
      state=$(virsh -c "$uri" domstate "$vm" 2>/dev/null || true)
      [[ $state == running ]] || virsh -c "$uri" start "$vm"
    done
    ;;
  stop)
    for vm in "${vms[@]}"; do
      state=$(virsh -c "$uri" domstate "$vm" 2>/dev/null || true)
      [[ $state != running ]] || virsh -c "$uri" shutdown "$vm"
    done
    deadline=$((SECONDS + 120))
    while (( SECONDS < deadline )); do
      running=0
      for vm in "${vms[@]}"; do
        [[ $(virsh -c "$uri" domstate "$vm" 2>/dev/null || true) == running ]] && running=$((running + 1))
      done
      (( running == 0 )) && exit 0
      sleep 2
    done
    echo "Some VMs did not stop; refusing to force-destroy them." >&2
    exit 1
    ;;
  *)
    echo "Usage: $0 {start|stop}" >&2
    exit 2
    ;;
esac
