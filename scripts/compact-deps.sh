#!/usr/bin/env bash
# Bootstrap the compact launcher before PyYAML/Jinja2 can be imported.
set -euo pipefail

if [[ $# -gt 1 || ${1:-} != '' && ${1:-} != --apply ]]; then
  echo 'Usage: ./lab deps [--apply]' >&2
  exit 2
fi
if [[ $(uname -m) != x86_64 ]] || ! grep -qx 'VERSION_ID="24.04"' /etc/os-release || ! grep -qx 'ID=ubuntu' /etc/os-release; then
  echo 'Compact v1 requires Ubuntu 24.04 x86-64.' >&2
  exit 1
fi
packages=(qemu-kvm qemu-utils libvirt-clients libvirt-daemon-system virtinst
  cloud-image-utils python3 python3-yaml python3-jinja2 openssh-client curl git iproute2 dbus-user-session)
printf 'Host packages: %s\n' "${packages[*]}"
echo 'Adds the invoking user to libvirt/kvm; enables libvirtd.'
echo 'Creates /var/lib/libvirt/images/srv6-mup only if absent; existing permissions are preserved.'
echo 'Does not install Docker/Go on the host or change host network/sysctl settings.'
if [[ ${1:-} != --apply ]]; then
  echo 'Plan only. To apply: sudo ./lab deps --apply'
  exit 0
fi
if [[ ${EUID} -ne 0 || -z ${SUDO_USER:-} || ${SUDO_USER:-} == root ]]; then
  echo 'Run from your normal account: sudo ./lab deps --apply' >&2
  exit 1
fi
getent passwd "$SUDO_USER" >/dev/null
# Refuse a preexisting symlink before any root-owned directory operation.
for directory in /var/lib/libvirt /var/lib/libvirt/images /var/lib/libvirt/images/srv6-mup; do
  if [[ -L $directory ]]; then
    echo "Refusing symlink directory: $directory" >&2
    exit 1
  fi
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y "${packages[@]}"
usermod -aG libvirt,kvm "$SUDO_USER"
if [[ ! -e /var/lib/libvirt/images/srv6-mup ]]; then
  install -d -o "$SUDO_USER" -g kvm -m 2770 /var/lib/libvirt/images/srv6-mup
fi
systemctl enable --now libvirtd
echo 'Log out and back in to refresh group membership, then run ./lab doctor.'
echo 'Custom image_directory paths must already exist and be writable by your account.'
