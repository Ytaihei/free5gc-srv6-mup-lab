# Clean-OS reproduction using nested KVM

English | [日本語](clean-room-reproduction.ja.md)

This procedure reproduces the **six-VM reference** inside a nested-KVM parent;
it is not the single-VM compact setup. Use [compact setup](compact-lab.md) and
[its recorded checkpoints](validation-summary.md) for that profile. A second
physical-host run remains unverified and outside the agreed acceptance scope,
not an outstanding mandatory source-publication gate.

This optional test uses a clean Ubuntu L1 host on the original x86-64 physical
machine, and recreates all six lab guests at L2. It proves recovery from
reviewed source on a clean OS, **not** portability to another physical CPU
or ARM/macOS. The original guest disks are preserved and never cloned into
the test. Source/container/toolchain pins apply; apt packages are not a fully
hermetic snapshot.

The operator must approve the temporary original-lab outage. On a 32 GiB
machine the 24 GiB test host and original 17 GiB guest set cannot run together.
Do not suspend/save the L1 with L2 guests running or migrate it during testing.
See the [kernel nested-KVM documentation](https://docs.kernel.org/virt/kvm/x86/running-nested-guests.html).

## Outer host

`config/repro-host.yml` defines a dedicated non-autostart VM, 12 vCPU, 24 GiB
RAM, a 280 GiB sparse disk, and an independent NAT management network. Review
its subnet for your environment. The helper checks available RAM, stopped
original guests, namespace/subnet collisions, free disk, nested KVM, and the
SHA256 of the pristine locked Noble image. It refuses to overwrite existing
domains, networks, or disks and does not automatically delete anything.

```bash
make down
python3 scripts/create-repro-host.py          # read-only safety checks
python3 scripts/create-repro-host.py --create
```

The base image is the checksum-verified distribution image already present
in the configured image directory, not any original lab guest disk. Only the
operator's **public** SSH key is installed. No GitHub token or private key is
copied. Keep host snapshots and raw test logs under ignored `artifacts/repro/`.

## Fresh source and bootstrap

Freeze a clean committed revision and export a history-free candidate using
[the distribution checks](source-distribution.md). Transfer only that reviewed
source archive; do not copy recovery Git history or remote credentials into the
test host. Record its checksum on the outer host:

```bash
python3 scripts/export-source.py --revision HEAD \
  --output artifacts/repro/source-candidate.tar.gz
sha256sum artifacts/repro/source-candidate.tar.gz
scp artifacts/repro/source-candidate.tar.gz labadmin@192.168.124.10:source-candidate.tar.gz
ssh labadmin@192.168.124.10
sudo cloud-init status --wait
sha256sum source-candidate.tar.gz           # must match the recorded outer-host digest
tar -xzf source-candidate.tar.gz            # on this fresh host, with no existing target directory
cd free5gc-srv6-mup-lab
python3 scripts/export-source.py --check-tree .
sudo env LAB_HOST_USER=labadmin ./scripts/install-host-deps.sh
./scripts/install-go-toolchain.sh
exit
```

Reconnect with a **new** SSH session so libvirt/kvm group membership is active.
Confirm `/dev/kvm` access and VMX/SVM CPU flags; do not silently substitute
QEMU software emulation. Use the tracked default lab configuration. The L2
management subnet is isolated inside L1 and may match the original lab's.

```bash
cd free5gc-srv6-mup-lab
make preflight
make bootstrap
make dashboard-install                      # no Tailscale: loopback only
make test-baseline
MUP_ENABLE=1 make test-mup
MUP_ENABLE=1 make test-lease
ONE_CALL_ENABLE=1 OBSERVE_SECONDS=2 make test-one-call
NETWORK_RECOVERY_ENABLE=1 make test-network-recovery
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml
```

Record exact source SHA, cloud image checksums, L1/L2 distro/kernel versions,
KVM domain type, Ansible recaps, E2E results, dashboard health, and a no-change
reconciliation. If a clean-bootstrap bug appears, fix the repository, update
the frozen revision, and rerun the affected provisioning. Document any retry;
do not claim a first-attempt clean pass after manual guest fixes.

For a second completely clean L1/L2 run, stop the first L2 set and L1 host,
then supply a complete ignored YAML profile to `create-repro-host.py --config`.
Choose a new VM name, network name, bridge, subnet, and MAC. Existing test disks
are retained; the helper never overwrites the previous run. Obtain the new
source revision using a newly reviewed source archive and repeat host setup and bootstrap.

The static SR locator routes and MUP PE (N6/Direct side) tenant VRF/N6 membership are declared in
Netplan, not solely in an imperative startup script. See the upstream
[Netplan VRF reference](https://netplan.readthedocs.io/en/stable/netplan-yaml/#properties-for-device-type-vrfs).
The network-recovery gate explicitly tests `netplan apply`, a
`systemd-networkd` restart, and eviction of only the configured DN's MUP PE (N6/Direct side)
neighbor entry, each followed by a full UE call and XDP counter checks.
The N6 connected route is also explicit in tenant table 100, and the fallback
gateway is declared on-link, so adopting an existing MUP PE (N6/Direct side) interface into the
VRF does not depend on an automatic connected route surviving the transition.
The addressless `mup-dn` master is optional for network-online readiness: its
N6 address belongs to the member interface. Otherwise networkd can forward
traffic while wait-online times out waiting for an address on the master.
The recovery gate also checks wait-online with a ten-second timeout.
If an existing MUP PE (N6/Direct side) retains a failed wait-online result from before this fix,
apply the playbook, verify readiness, and rerun the unit (do not just clear its
failure flag):

```bash
source scripts/lab-lib.sh
lab_ssh "$LAB_NPE" '/lib/systemd/systemd-networkd-wait-online --timeout=10 && sudo systemctl restart systemd-networkd-wait-online'
```

The pinned Vinbero End.DT4 path needs a resolved DN neighbor for XDP forwarding.
A one-time startup probe is insufficient if the cache is removed later.
On the MUP PE (N6/Direct side) only, `vinbero-neighbor-refresh.timer` sends one MUP PE (N6/Direct side)-originated
ICMP probe to the configured DN in `mup-dn` every 15 seconds (two-second
timeout). It also runs shortly after activation, without assuming the DN is
already provisioned. This bounds neighbor recovery when the DN and N6 network
are healthy; it is not a general solution for arbitrary tenant destinations.
It does not send UE traffic, install permanent MAC entries, or replace the
dashboard's UE probe. The eviction test waits for automatic resolution without
manually priming ARP, then verifies real UE ICMP/HTTP and both PE counters.

## Restore the original lab

Inside L1 run `make down` and leave the SSH session. On the physical host:

```bash
virsh -c qemu:///system shutdown srv6-mup-repro-host
virsh -c qemu:///system domstate srv6-mup-repro-host
# Wait until the state is "shut off" before continuing.
make up
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml
make test-baseline
```

Verify the original dashboard and MUP path before handing back the machine.
If graceful shutdown fails, stop and inspect; do not force-delete either lab.
The test disk/network are retained, without autostart. Publication, PR merge,
and distribution of guest disks or binaries require separate review/approval.

### Recovery after an original-guest kernel update

The guest apt/kernel set is not fully pinned. A new core kernel can lack the
gtp5g module built for the previous ABI. Run the Ansible playbook first to
build/install the module for the running kernel. If UPF failed at boot and
SMF still cannot select a usable UPF afterward, restart the complete core,
then the passive observer, then RAN. This resets active lab sessions; it is
an explicit recovery operation, not a routine no-change reconciliation:

```bash
source scripts/lab-lib.sh
lab_ssh "$LAB_RAN" 'sudo systemctl stop ueransim-ue ueransim-gnb'
lab_ssh "$LAB_CORE" 'sudo modprobe gtp5g && sudo systemctl restart free5gc-lab && sudo systemctl restart pfcp-observer'
lab_ssh "$LAB_RAN" 'sudo systemctl start ueransim-gnb && sudo systemctl start ueransim-ue'
make test-baseline
MUP_ENABLE=1 make test-mup
MUP_ENABLE=1 make test-lease
ONE_CALL_ENABLE=1 make test-one-call
```

Automatic recovery across arbitrary kernel/apt upgrades is not part of the
clean pinned-image reproduction claim. Do not change image digests or suppress
the failing checks to hide such a difference.
