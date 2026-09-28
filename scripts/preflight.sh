#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/.." && pwd)
source "$root_dir/scripts/host-memory.sh"
lab_config=("$root_dir/scripts/lab-config.py")
required_go=$(awk '/^go / {print $2; exit}' "$root_dir/go.mod")
failures=()
warnings=()

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failures+=("$1"); }
warn() { printf 'WARN  %s\n' "$1"; warnings+=("$1"); }

if [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]]; then
  pass 'Linux x86-64 host'
else
  fail "unsupported host: $(uname -s) $(uname -m)"
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck source=/etc/os-release
  source /etc/os-release
  if [[ ${ID:-} == ubuntu ]]; then
    pass "Ubuntu ${VERSION_ID:-unknown}"
  else
    warn "host OS is ${PRETTY_NAME:-unknown}; package automation targets Ubuntu"
  fi
fi

commands=(ansible-playbook bash curl git jq make python3 qemu-img sha256sum ssh ssh-keygen virsh virt-install)
memory_mib=$(awk '/^MemTotal:/ {printf "%d", $2 / 1024}' /proc/meminfo)
if memory_error=$(lab_require_host_memory "$memory_mib" 2>&1); then
  if (( memory_mib >= 24576 )); then
    pass "memory ${memory_mib} MiB"
  else
    warn "memory ${memory_mib} MiB meets minimum but is below the recommended 24576 MiB"
  fi
else
  fail "$memory_error"
fi
for command in "${commands[@]}"; do
  if command -v "$command" >/dev/null 2>&1; then
    pass "command $command"
  else
    fail "missing command $command; run scripts/install-host-deps.sh"
  fi
done

if python3 -c 'import jinja2, yaml' >/dev/null 2>&1; then
  pass 'Python Jinja2 and YAML modules'
else
  fail 'missing Python Jinja2/PyYAML modules'
fi

if "${lab_config[@]}" validate >/dev/null 2>&1; then
  pass "lab configuration $("${lab_config[@]}" path)"
else
  fail 'invalid lab configuration; run scripts/lab-config.py validate'
  printf '\nPreflight result: %d failure(s), %d warning(s)\n' "${#failures[@]}" "${#warnings[@]}"
  exit 1
fi

cache_home=${XDG_CACHE_HOME:-${HOME}/.cache}
locked_go=${cache_home}/srv6-mup/go/${required_go}/bin/go
legacy_go=${HOME}/.cache/srv6-mup/go/bin/go
go_bin=${GO:-}
if [[ -z $go_bin && -x $locked_go ]]; then go_bin=$locked_go; fi
if [[ -z $go_bin && -x $legacy_go ]]; then go_bin=$legacy_go; fi
if [[ -z $go_bin ]] && command -v go >/dev/null 2>&1; then go_bin=$(command -v go); fi
if [[ -n $go_bin && $("$go_bin" version 2>/dev/null) == *"go${required_go}"* ]]; then
  pass "Go ${required_go}"
else
  fail "Go ${required_go} is unavailable; run scripts/install-go-toolchain.sh"
fi

if [[ -e /dev/kvm && -r /dev/kvm && -w /dev/kvm ]]; then
  pass '/dev/kvm is accessible'
else
  fail '/dev/kvm is missing or inaccessible'
fi

for group in kvm libvirt; do
  if id -nG | tr ' ' '\n' | grep -Fxq "$group"; then
    pass "current user belongs to $group"
  else
    fail "current user is not in $group; log out after dependency installation"
  fi
done

libvirt_uri=$("${lab_config[@]}" get host.libvirt_uri)
if virsh -c "$libvirt_uri" uri >/dev/null 2>&1; then
  pass 'libvirt system connection'
else
  fail "cannot connect to $libvirt_uri"
fi

image_directory=$("${lab_config[@]}" get host.image_directory --expand-path)
image_parent=$image_directory
while [[ ! -e $image_parent && $image_parent != / ]]; do
  image_parent=$(dirname "$image_parent")
done
available_gib=$(df -Pk "$image_parent" | awk 'NR == 2 {printf "%d", $4 / 1024 / 1024}')
if (( available_gib >= 200 )); then
  pass "image filesystem has ${available_gib} GiB available"
else
  warn "image filesystem has ${available_gib} GiB available; 200 GiB is recommended"
fi

printf '\nPreflight result: %d failure(s), %d warning(s)\n' "${#failures[@]}" "${#warnings[@]}"
if ((${#failures[@]})); then
  exit 1
fi
if [[ ${PREFLIGHT_STRICT:-0} == 1 && ${#warnings[@]} -gt 0 ]]; then
  exit 2
fi
