# free5GC SRv6 MUP lab

English | [日本語](README.ja.md)

This repository builds an isolated IPv4 Direct MUP lab with free5GC,
UERANSIM, a passive N4/PFCP observer, an in-process GoBGP MUP controller, and
Vinbero's eBPF/XDP SRv6 data plane. The lab user plane is isolated from the
physical LAN. This is an experimental open implementation, not a production
5G core or a claim of complete MUP standards compliance.

Start with the [glossary](docs/glossary.md) for terminology and the
[feature status table](docs/feature-status.md) for implemented, partial and
missing capabilities, with verification evidence tracked separately.

For a single-VM setup with component-level customization, start with the
[compact setup guide](docs/compact-lab.md) and
[prebuilt/development workflow](docs/prebuilt-development.md).
The source-build path works today; prebuilt distribution remains gated by image review.

## Architecture

Both packet edges are **MUP PEs**: **MUP PE (N3/Interwork side)** and
**MUP PE (N6/Direct side)**. The compatible internal IDs remain `tpe` and `npe`
(VM names `lab-tpe` and `lab-npe`). These IDs are not MUP specification terms.

```text
                         BGP MUP SAFI (AFI 1 / SAFI 85)
                   +---------------------------------------+
                   |                                       |
             lab-tpe 192.168.123.12                  lab-npe .13
             MUP PE (Vinbero)                       MUP PE (Vinbero)
             N3/Interwork side                      N6/Direct side
                   |          SRv6 underlay                |
 gNB .11 -- N3a -- +==== 2001:db8:100:10::/64 ============+ -- N6 -- DN .15
                   |                                       |
                   + -- N3 core -- ordinary UPF .10 -------+

 SMF <-- N4/PFCP --> UPF       lab-core, Docker bridge br-free5gc
                         ^ passive AF_PACKET capture
                         |
                  pfcp-observer -- snapshot/lease --> lab-mupc .14
                                                    MUP-C + GoBGP
```

The observer commits only accepted PFCP establishment/modification/deletion
transactions. MUP-C applies the configured DNN/UE/SUPI policy and originates a
T1 Session Transformed route for downlink plus a T2 Session Transformed route
for uplink. Vinbero resolves those routes against the MUP PE (N3/Interwork side)'s Interwork Segment
Discovery route and the MUP PE (N6/Direct side)'s Direct Segment Discovery route, then programs
its eBPF maps.

Selected traffic uses the SRv6 MUP bypass. Unmatched traffic, withdrawn routes
and expired observer state use the kernel/ordinary-UPF path. Successful
fallback still requires a valid UPF session and routes; an unknown TEID is not
necessarily accepted by the UPF. No TEID is entered manually.

See [docs/architecture.md](docs/architecture.md) for the route and packet
walks, and [docs/address-plan.md](docs/address-plan.md) for exact addresses,
RDs, RTs, locators, and SIDs.

## Implemented components

- Six non-autostart libvirt VMs and isolated user-plane networks
- free5GC v4.2.3 with gtp5g v0.9.5 and UERANSIM v3.3.0
- Passive PFCP session reconstruction on `br-free5gc`
- Policy-driven MUP-C, GoBGP v4.9.0, Connect RPC, and `mupctl`
- Vinbero v0.1.1 patched to use upstream GoBGP v4.8
- Vinbero driver-mode XDP on the virtio PE interfaces, with patched eBPF objects regenerated during provisioning
- Paired T1/T2 advertisement with local rollback on partial failure, and
  15-second observer lease withdrawal; not simultaneous atomic installation on both PEs
- MUP PE (N3/Interwork side) `End.M.GTP4.E`, MUP PE (N6/Direct side) `End.DT4`, ISD/DSD resolution, and direct/fallback paths
- Baseline and MUP E2E test scripts, including withdrawal fallback
- Observer lease-expiry/recovery test with fresh PFCP re-registration
- Configuration-read-only live dashboard for topology, PFCP sessions, MUP
  routes, service health, Vinbero XDP counters, and a five-second UE-to-DN ICMP
  probe

Ansible deploys the `vinbero_pe` role. Migration guards disable old VPP/sidecar
services on reused guests without removing their packages; that legacy source
is not part of the public distribution.

## Lifecycle

Host and network settings are centralized in `config/lab.example.yml`.
Copy it to ignored `config/lab.local.yml` for another machine. Read
[portable configuration](docs/portable-configuration.md) before changing an
existing topology; configuration is not an automatic live migration mechanism.

For independent clean-OS recovery testing on the same physical host, see
[nested-KVM reproduction](docs/clean-room-reproduction.md). This optional test
preserves the original lab but requires a temporary outage.
The [validation summary](docs/validation-summary.md) records the tested
conditions and scope limitations without private host or workflow records.

```bash
cd free5gc-srv6-mup-lab
./scripts/install-go-toolchain.sh
make preflight
make check
make bootstrap
make test-baseline
MUP_ENABLE=1 make test-mup
MUP_ENABLE=1 make test-lease
ONE_CALL_ENABLE=1 make test-one-call
```

Useful controller commands run on `lab-mupc`:

```bash
mupctl status
mupctl sessions
mupctl suppress <session-key>
mupctl resume <session-key>
mupctl reconcile
```

## Live dashboard

The dashboard is installed as a user service and refreshes the lab state every
five seconds. It is available locally and, when `tailscale0` exists, across the
encrypted Tailscale network:

- Local: `http://127.0.0.1:8787/`
- Tailnet: `http://<tailscale-hostname>:8787/`

Install or update it with `make dashboard-install`. The service binds to
loopback and the IPv4 address assigned to `tailscale0`; it does not bind the
physical LAN or a wildcard address. The JSON snapshot is available at
`/api/state` for additional local tooling.

The dashboard sends one ICMP echo from `uesimtun0` to `10.210.6.15` on each
five-second collection. Its topology animates the request/reply on the active
MUP or fallback path and shows the measured RTT; a timeout turns the U-Plane
links red. This is the only active monitoring traffic generated by the
dashboard. All controller, BGP, PFCP, and Vinbero access remains read-only.

To watch a complete UE lifecycle, keep the dashboard open and run
`ONE_CALL_ENABLE=1 make test-one-call`. The script withdraws the current UE,
pauses for the dashboard, performs a fresh Initial Registration and PDU Session
establishment, waits for PFCP-derived T1/T2 programming, and proves ICMP/HTTP
traffic while checking that both PE XDP redirect counters increase. Set
`OBSERVE_SECONDS` to change the default eight-second observation pauses.

For offline inspection, open the [reviewed one-call PCAP example](examples/pcap/one-call/README.md).
It includes N2/N4/BGP/N3/SRv6/N6 traces and a merged view, with bilingual notes
on retained lab identifiers, privacy review and decoder limitations.

Full installation, recovery, and validation procedures are in
[docs/operations.md](docs/operations.md). Claim boundaries and remaining
research tests are in [docs/research-scope.md](docs/research-scope.md). Future
MUP work remains pending in [the research backlog](docs/research-backlog.md).
CI coverage, local scanner commands, SBOMs and their limitations are documented
in [supply-chain checks](docs/supply-chain.md).

## License and distribution

An experimental [single-VM Compose profile](docs/compact-lab.md) is being
validated to simplify setup. It is not yet the default or a prebuilt release;
the reference procedures above still use six VMs.

Original code and configuration in this repository are licensed under the
Apache License 2.0. Runtime projects fetched during provisioning retain their
own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Until the
distribution milestone is complete, releases are source-only and must not
include VM images, container archives, or compiled third-party binaries.
The [source distribution workflow](docs/source-distribution.md) describes the
explicit public inventory, privacy checks and history-free export tool.

The authoritative license is [LICENSE](LICENSE); an
[unofficial Japanese reference translation](LICENSE.ja.md) is also available.

## Contact

Maintainer: [Ytaihei](https://github.com/Ytaihei). Contact:
[taihei@sfc.wide.ad.jp](mailto:taihei@sfc.wide.ad.jp).

Use [GitHub Issues](https://github.com/Ytaihei/free5gc-srv6-mup-lab/issues)
for ordinary bug reports. Send sensitive security reports privately by email
or through GitHub private vulnerability reporting; see [SECURITY.md](SECURITY.md).
Do not include credentials, private lab details or unreviewed captures in public issues.
