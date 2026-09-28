#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
source "$root_dir/scripts/host-memory.sh"
lab_require_host_memory
lab_config=("$root_dir/scripts/lab-config.py")
uri=$("${lab_config[@]}" get host.libvirt_uri)
image_dir=$("${lab_config[@]}" get host.image_directory --expand-path)
generated_dir=${root_dir}/infra/cloud-init/generated
lock_value=(python3 "${root_dir}/scripts/lock-value.py")
mkdir -p "$generated_dir"

for command in curl python3 qemu-img ssh-keygen virt-install virsh; do
  command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
python3 -c 'import yaml' >/dev/null 2>&1 || {
  echo 'Missing Python YAML module; run scripts/install-host-deps.sh' >&2
  exit 1
}

ssh_key=$("${lab_config[@]}" get host.ssh_public_key --expand-path)
ssh_private_key=$("${lab_config[@]}" get host.ssh_private_key --expand-path)
guest_user=$("${lab_config[@]}" get host.ansible_user)
if [[ ! -f $ssh_key ]]; then
  [[ ! -e $ssh_private_key ]] || { echo 'Private key exists but public key is missing; restore its .pub file first.' >&2; exit 1; }
  install -d -m 0700 "$(dirname "$ssh_private_key")"
  ssh-keygen -q -t ed25519 -N '' -f "$ssh_private_key"
fi
[[ -r $ssh_private_key && -d $image_dir && -w $image_dir ]] || {
  echo 'Private key must be readable and image_directory must exist and be writable; run host dependency setup.' >&2
  exit 1
}
# Discard comments and validate the public key before inserting it into YAML.
ssh-keygen -l -f "$ssh_key" >/dev/null
public_key=$(awk 'NR == 1 {print $1 " " $2}' "$ssh_key")
[[ $public_key =~ ^ssh-(ed25519|rsa)\ [A-Za-z0-9+/=]+$ ]] || {
  echo 'Expected an Ed25519 or RSA public key.' >&2; exit 1;
}

fetch_image() {
  local release=$1 url=$2 expected=$3
  local file=${release}-server-cloudimg-amd64.img
  if [[ ! -f ${image_dir}/${file} ]]; then
    curl -fL --retry 3 -o "${image_dir}/${file}.partial" "$url"
    local actual
    actual=$(sha256sum "${image_dir}/${file}.partial" | awk '{print $1}')
    [[ $actual == "$expected" ]] || {
      echo "Checksum mismatch for downloaded $file" >&2
      exit 1
    }
    mv "${image_dir}/${file}.partial" "${image_dir}/${file}"
  fi
  local actual
  actual=$(sha256sum "${image_dir}/${file}" | awk '{print $1}')
  [[ $actual == "$expected" ]] || {
    echo "Cached image $file does not match config/versions.lock.yml" >&2
    exit 1
  }
  echo "${image_dir}/${file}"
}

jammy_image=$(fetch_image jammy \
  "$("${lock_value[@]}" cloud_images.jammy.url)" \
  "$("${lock_value[@]}" cloud_images.jammy.sha256)")
noble_image=$(fetch_image noble \
  "$("${lock_value[@]}" cloud_images.noble.url)" \
  "$("${lock_value[@]}" cloud_images.noble.sha256)")

make_cloud_init() {
  local name=$1
  sed -e "s|__HOSTNAME__|${name}|g" -e "s|__USER__|${guest_user}|g" -e "s|__SSH_KEY__|${public_key}|g" \
    "${root_dir}/infra/cloud-init/user-data.yml.in" > "${generated_dir}/${name}-user-data.yml"
  printf 'instance-id: %s\nlocal-hostname: %s\n' "$name" "$name" > "${generated_dir}/${name}-meta-data.yml"
}

create_vm() {
  local name=$1 memory=$2 vcpus=$3 disk_size=$4 base_image=$5 os_variant=$6 mac=$7
  shift 7
  if virsh -c "$uri" dominfo "$name" >/dev/null 2>&1; then
    echo "$name: already defined"
    return
  fi
  local disk=${image_dir}/${name}.qcow2
  [[ -f $disk ]] || qemu-img create -q -f qcow2 -F qcow2 -b "$base_image" "$disk" "${disk_size}G"
  chgrp kvm "$disk"
  chmod 0660 "$disk"
  make_cloud_init "$name"
  network_args=(--network "network=${LAB_MANAGEMENT_NETWORK},model=virtio,mac=${mac}")
  for network_spec in "$@"; do
    network=${network_spec%%=*}
    if [[ $network_spec == *=* ]]; then
      network_args+=(--network "network=${network},model=virtio,mac=${network_spec#*=}")
    else
      network_args+=(--network "network=${network},model=virtio")
    fi
  done
  virt-install --connect "$uri" \
    --name "$name" --memory "$memory" --vcpus "$vcpus" --cpu host-passthrough \
    --os-variant "$os_variant" --import --graphics none --noautoconsole \
    --disk "path=${disk},format=qcow2,bus=virtio" \
    "${network_args[@]}" \
    --cloud-init "user-data=${generated_dir}/${name}-user-data.yml,meta-data=${generated_dir}/${name}-meta-data.yml"
}

mgmt_network=$("${lab_config[@]}" get networks.management.name)
n2_network=$("${lab_config[@]}" get networks.n2.name)
n3_access_network=$("${lab_config[@]}" get networks.n3_access.name)
n3_core_network=$("${lab_config[@]}" get networks.n3_core.name)
sr_network=$("${lab_config[@]}" get networks.sr_underlay.name)
n6_network=$("${lab_config[@]}" get networks.n6.name)

# The management network is passed through the environment because create_vm
# builds that NIC before its variable-length data-plane NIC list.
LAB_MANAGEMENT_NETWORK=$mgmt_network
export LAB_MANAGEMENT_NETWORK

create_vm "$("${lab_config[@]}" get nodes.core.name)" 8192 6 80 "$jammy_image" ubuntu22.04 \
  "$("${lab_config[@]}" get nodes.core.management_mac)" "$n2_network" "$n3_core_network" "$n6_network"
create_vm "$("${lab_config[@]}" get nodes.ran.name)" 2048 2 20 "$noble_image" ubuntu24.04 \
  "$("${lab_config[@]}" get nodes.ran.management_mac)" "$n2_network" "$n3_access_network"
create_vm "$("${lab_config[@]}" get nodes.tpe.name)" 2048 2 20 "$noble_image" ubuntu24.04 \
  "$("${lab_config[@]}" get nodes.tpe.management_mac)" "$n3_access_network" "$sr_network" \
  "$n3_core_network=$("${lab_config[@]}" get nodes.tpe.n3_core_mac)"
create_vm "$("${lab_config[@]}" get nodes.npe.name)" 2048 2 20 "$noble_image" ubuntu24.04 \
  "$("${lab_config[@]}" get nodes.npe.management_mac)" "$sr_network" "$n6_network"
create_vm "$("${lab_config[@]}" get nodes.mupc.name)" 2048 2 20 "$noble_image" ubuntu24.04 \
  "$("${lab_config[@]}" get nodes.mupc.management_mac)"
create_vm "$("${lab_config[@]}" get nodes.dn.name)" 1024 1 10 "$noble_image" ubuntu24.04 \
  "$("${lab_config[@]}" get nodes.dn.management_mac)" "$n6_network"

"${root_dir}/infra/libvirt/reconcile-vm-interfaces.sh"

echo "VMs defined. Wait for cloud-init, then run: ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml"
