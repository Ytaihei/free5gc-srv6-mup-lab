#!/usr/bin/env bash
# Shared guard for the fixed six-VM profile. Swap is not physical capacity.

lab_require_host_memory() {
  local total_mib=${1:-}
  local guest_mib=17408 reserve_mib=2048
  if [[ -z $total_mib ]]; then
    total_mib=$(awk '/^MemTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo)
  fi
  if [[ ! $total_mib =~ ^[0-9]+$ ]]; then
    echo 'Cannot determine physical host memory' >&2
    return 1
  fi
  if (( total_mib < guest_mib + reserve_mib )); then
    echo "Physical memory ${total_mib} MiB is insufficient: six VMs require ${guest_mib} MiB plus ${reserve_mib} MiB reserved for the host. No VM startup is permitted." >&2
    return 1
  fi
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  lab_require_host_memory
fi
