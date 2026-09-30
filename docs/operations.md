# Operations

English | [日本語](operations.ja.md)

This page is for the **six-VM reference profile only**. For the single-VM
Compose profile, use [compact setup](compact-lab.md) and the [hands-on guide](hands-on.md).
Do not mix their deployment commands, interfaces, dashboard ports or XDP checks.

## First installation and reconciliation

Read [portable configuration](portable-configuration.md) and prepare the local
configuration before installing. The example topology is the default; guest
names/users/addresses in commands below refer to that example.

Host dependencies require administrator privileges:

```bash
cd free5gc-srv6-mup-lab
sudo LAB_HOST_USER="$USER" ./scripts/install-host-deps.sh
./scripts/install-go-toolchain.sh
make preflight
```

Log out and back in once so `libvirt` and `kvm` group membership is active.
Run `make preflight` again after logging in; it is read-only and reports all
missing commands, group membership, KVM access, memory, and disk-space warnings.
Then build or reconcile the isolated lab:

```bash
make check
make networks
./infra/libvirt/create-vms.sh
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml
```

`create-vms.sh` is non-destructive for existing guests. Its reconciliation step
adds N3 core to the MUP PE (N3/Interwork side) and moves the MUP PE (N6/Direct side) data-facing NIC from N3 core to N6.
When changing an already-provisioned topology, inspect `virsh domiflist` first.
If reconciliation restarts free5GC, Ansible restarts the gNB and UE only after
the passive PFCP observer is ready; this renews the SCTP and PFCP state that
UERANSIM cannot recover across an AMF restart by itself.
An observer restart or binary update also reconnects an already-installed RAN
after capture is ready, so passive PFCP state is repopulated. A no-change run
does not restart the RAN. On a clean bootstrap, absent RAN units are skipped
and started by the later UERANSIM play.

## Validation

Start with the conventional UPF path:

```bash
make test-baseline
```

The script waits for a PFCP-derived UE session, suppresses its MUP routes, then
tests ICMP and HTTP between the UE and DN through the ordinary UPF. It restores
the session's prior eligible state on exit.

Run the route-driven bypass test explicitly:

```bash
MUP_ENABLE=1 make test-mup
```

This resumes the observed session, waits for the T1/T2 pair, checks Vinbero's
MUP PE (N6/Direct side) downlink and MUP PE (N3/Interwork side) uplink map state, tests UE traffic, withdraws the pair,
and verifies that the same PFCP session falls back to the UPF before restoring
the MUP state.

Run the disruptive observer-lease test explicitly:

```bash
MUP_ENABLE=1 make test-lease
```

It stops the observer, verifies 15-second lease expiry and MUP withdrawal,
proves the ordinary UPF path remains usable, then restarts the observer and UE
so fresh PFCP state restores the bypass. The UE address may change.

Useful diagnostics:

```bash
ssh ubuntu@192.168.123.14 sudo mupctl status
ssh ubuntu@192.168.123.14 sudo mupctl sessions
ssh ubuntu@192.168.123.12 sudo systemctl status vinbero vinbero-bootstrap
ssh ubuntu@192.168.123.13 sudo systemctl status vinbero vinbero-bootstrap
ssh ubuntu@192.168.123.10 sudo systemctl status free5gc-lab pfcp-observer
```

## Live dashboard

Build, install, enable, and restart the host-side dashboard:

```bash
cd free5gc-srv6-mup-lab
make dashboard-install
systemctl --user status srv6-mup-dashboard
```

Endpoints:

```text
http://127.0.0.1:8787/
http://<tailscale-hostname>:8787/
```

The latter endpoint is reachable only from the Tailnet. Transport over
Tailscale is encrypted by WireGuard; the application listener itself is plain
HTTP and is bound specifically to the current IPv4 address on `tailscale0`, not
the physical LAN.
The service resolves the current `tailscale0` IPv4 address on each restart, so
the listener follows a future Tailscale address change automatically.

Useful checks:

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/api/state | jq .
ss -ltnp | grep ':8787'
journalctl --user -u srv6-mup-dashboard -f
```

Controller and PE collection is read-only. It uses the MUP-C Connect API plus
fixed SSH commands for systemd state and Vinbero JSON counters/routes. In
addition, every five seconds it sends one ICMP echo from UE `uesimtun0` to DN
`10.210.6.15`. The topology animates the request/reply and reports RTT on
success; it stops the animation and marks the U-Plane red on failure. SSH
sessions use the existing `~/.ssh/id_ed25519` lab access and are multiplexed
for 60 seconds.

Tailscale Serve can provide a certificate and HTTPS on the node name after an
owner enables Serve for the Tailnet. Once enabled, keep the loopback listener
and run:

```bash
tailscale serve --bg --yes 8787
tailscale serve status
```

The direct `tailscale0:8787` listener remains the no-admin-change fallback.

### Watch a UE 1call

Open the dashboard, then run the disruptive-to-the-lab-UE test explicitly:

```bash
cd free5gc-srv6-mup-lab
ONE_CALL_ENABLE=1 make test-one-call
```

The expected dashboard sequence is:

1. UE switch-off de-registration removes `uesimtun0`; PFCP sessions and MUP
   routes fall to zero, and `lab-ran` is degraded.
2. A fresh Initial Registration and PDU Session create a new UE address/F-TEID
   state. The observer selects it and MUP-C advertises a paired T1/T2.
3. The MUP path becomes active. ICMP and HTTP traffic then increase the MUP PE (N3/Interwork side)
   and MUP PE (N6/Direct side) `REDIRECT` counters.

The default pause at each visually interesting state is eight seconds, which is
longer than the dashboard's five-second refresh interval. It can be changed or
disabled:

```bash
OBSERVE_SECONDS=15 ONE_CALL_ENABLE=1 make test-one-call
OBSERVE_SECONDS=0 ONE_CALL_ENABLE=1 make test-one-call
```

The script always attempts to start `ueransim-ue` again when interrupted or
when an assertion fails. It leaves the successfully registered UE and its MUP
routes active after a passing run.

Vinbero CLI calls use a loopback-only API:

```bash
sudo env VINBERO_SERVER=http://127.0.0.1:8080 vinbero mup list
sudo env VINBERO_SERVER=http://127.0.0.1:8080 vinbero headend-v4 list
sudo env VINBERO_SERVER=http://127.0.0.1:8080 vinbero sid list
```

Both PE data interfaces should show `xdp` (driver mode), not `xdpgeneric`:

```bash
ip -details link show enp2s0
```

## Controller operations

Run on `lab-mupc`:

```bash
mupctl status
mupctl sessions
mupctl suppress <session-key>  # withdraw T1/T2, leave PFCP session intact
mupctl resume <session-key>    # re-evaluate policy and advertise
mupctl reconcile
```

Snapshots arrive immediately after accepted PFCP changes and every 5 seconds.
If no snapshot arrives for 15 seconds, MUP-C withdraws advertised session
routes. Restarting the observer does not reconstruct sessions that were
established before capture began; re-register the UE to generate a new complete
PFCP establishment transaction.

## Recovery and migration

- The lab VMs do not autostart. Use `make up` and `make down` to manage them.
- The `vinbero_pe` migration guards disable former VPP/GoBGP-sidecar services
  on reused guests. Their packages are not automatically removed; the old
  implementation is excluded from the public source inventory.
- Unreviewed historical free5GC research patches are not applied to the pinned
  upstream baseline and are not part of the public source distribution.
- Unrelated host workloads and machine-specific backups are not lab prerequisites
  and are outside the public distribution.
