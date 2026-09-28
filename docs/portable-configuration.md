# Portable lab configuration

English | [日本語](portable-configuration.ja.md)

The supported deployment is six x86-64 KVM/libvirt VMs on Ubuntu 24.04,
with free5gc-compose inside the core VM. Compose alone cannot reproduce the
kernel modules, isolated interfaces and Vinbero XDP data plane. Libvirt XML,
cloud-init and Ansible supply those layers; no running VM image is distributed.
The fixed profile requires 17,408 MiB of guest RAM plus at least 2,048 MiB of
physical host reserve. Preflight, VM creation and VM startup reject a host
below that minimum; swap does not count. At least 24 GiB is recommended.

## Configuration entry point

Copy `config/lab.example.yml` to the ignored `config/lab.local.yml`, edit it,
and run `./scripts/lab-config.py validate`. Alternatively export `LAB_CONFIG`
with an absolute filename. Selection order is `LAB_CONFIG`, `lab.local.yml`,
then the tracked example. A local file is a **complete configuration**, not a
partial overlay. Missing and unknown keys fail validation.

The same configuration drives:

- host image directory, SSH key and guest administrator;
- VM names, management DHCP reservations and libvirt bridge definitions;
- Ansible's dynamic inventory and guest netplan;
- AMF/UPF/SMF N2/N3 addressing, one PLMN, one slice, one DNN and an IPv4 UE pool;
- UERANSIM, MUP policy, BGP identity/RDs/RTs and Vinbero SID settings;
- test endpoints, dashboard collection and optional Tailscale listening.

Use the public example subscriber only in an isolated lab. Never put a real
subscriber key or local credentials in the tracked example. Machine-specific
overrides and generated dashboard environment files must not be committed.

## What is intentionally fixed

This is a single-host, single-UE IPv4 Direct MUP topology, not a general network
orchestrator. The VM interface order, guest software installation directories,
the internal free5GC Docker network (`10.100.200.0/24`), N4 capture interface,
API port 9443, BGP port 179, and SID bit allocation remain fixed. Validation
rejects unsupported BGP/TEID-prefix and locator-length settings. Names and
paths use a restricted character set (no spaces or shell syntax).

Guest administrator, management address/MAC, VM/network name changes are for
**new deployments**. Existing guests are not renamed and cloud-init is not
rerun on them. Do not edit these values expecting a live migration. The network
definer refuses to redefine an active network whose managed settings differ;
plan a shutdown of affected guests/network before a topology change. It never
automatically destroys guests or their disks.

Provisioning reads pristine free5GC configuration from the locked Git commit
and renders it on every run. It does not apply one-shot hostname substitutions
to already-modified YAML. Old rendered files are backed up on the core guest.
Subscriber changes reconcile through the WebUI API. The one-slice lab does
not preserve the unused multi-PLMN/multi-slice examples from upstream.

## Host setup and dashboard

Set `LAB_CONFIG` explicitly when invoking the root installer with a custom
configuration, for example `sudo env LAB_CONFIG=/absolute/lab.local.yml
LAB_HOST_USER="$USER" ./scripts/install-host-deps.sh`. The image directory
must be an absolute, dedicated directory. SSH private/public paths must be a
pair; a missing public key is not regenerated over an existing private key.

The dashboard only accepts a loopback primary listener. Tailscale is optional:
set `host.dashboard.tailscale.enabled: false` for loopback-only operation. If
enabled but its IPv4 interface is absent at installation, the installer warns
and installs loopback-only; rerun after connecting Tailscale. It never binds a
wildcard or physical LAN address. Restart/reinstall after changing settings.

`mupctl` on a provisioned core/controller guest reads the non-secret
`/etc/srv6-mup-controller-url`; `--server` and `MUP_CONTROLLER` override it.
The service's private configuration does not need broader filesystem access.

## Evidence and remaining boundary

Configuration rendering and invalid-input cases are covered by unit tests.
Default-profile reconciliation is tested on the existing six-VM host. This
does **not** establish successful clean-room recovery on another machine, or
end-to-end operation for every nondefault combination. R5 remains open until
a second supported KVM host passes the complete test sequence.
