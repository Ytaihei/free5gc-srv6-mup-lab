#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

host_user=${LAB_HOST_USER:-${SUDO_USER:-}}
if [[ -z $host_user || $host_user == root ]]; then
  echo 'Set LAB_HOST_USER to the non-root account that will run the lab.' >&2
  exit 1
fi
getent passwd "$host_user" >/dev/null || {
  echo "Unknown LAB_HOST_USER: $host_user" >&2
  exit 1
}

export DEBIAN_FRONTEND=noninteractive
root_dir=$(cd "$(dirname "$0")/.." && pwd)
apt-get update
apt-get install -y \
  ansible bridge-utils cloud-image-utils curl git jq make openssh-client \
  libvirt-clients libvirt-daemon-system ovmf psmisc qemu-kvm qemu-utils \
  python3-jinja2 python3-yaml rsync shellcheck virtinst

usermod -aG libvirt,kvm "$host_user"
image_directory=$("$root_dir/scripts/lab-config.py" get host.image_directory --expand-path)
install -d -o "$host_user" -g kvm -m 2770 "$image_directory"
systemctl enable --now libvirtd

echo "Lab dependencies installed for $host_user."
echo 'Log out and back in before using libvirt for the first time.'
echo 'Only SRv6 MUP lab prerequisites are installed; unrelated host workloads are not configured.'
