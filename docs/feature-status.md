# Feature implementation and verification status

English | [日本語](feature-status.ja.md)

Reviewed: 2026-09-15. This is a manually reviewed snapshot of the tracked lab
profile and recorded evidence, not live dashboard state. Adding this document
did not rerun the privileged forwarding tests. The [validation summary](validation-summary.md)
describes historical runs; the [glossary](glossary.md) explains the terms below.

## How to read the two status columns

Implementation and verification are independent:

| Implementation | Meaning |
|---|---|
| Implemented | The stated capability is provided in the current lab profile, within the row's limits |
| Partial | A building block exists, but the complete stated capability is not provided |
| Not implemented | The lab does not provide this capability/profile; this does not assert that every upstream library lacks it |

| Verification | Meaning |
|---|---|
| Recorded lab test | A corresponding historical live run or packet capture is recorded; it applies only to the stated scenario |
| Offline tests only | Matching automated source, unit or packaging checks exist, without a corresponding live claim for this row |
| Unverified | No matching evidence is recorded for the complete stated scenario; this is not synonymous with absent code |

The supported baseline is six x86-64 KVM guests on one Ubuntu 24.04 host,
one IPv4 UE, an N3 Interwork attachment and an N6 Direct Segment. Session
routes use AFI 1 / SAFI 85, exact 32-bit TEIDs and a fixed SID argument layout.
The exact draft revisions and claim levels remain in [research scope](research-scope.md).
All extensions in [the research backlog](research-backlog.md) remain pending;
this table neither starts them nor promises delivery dates.

## Control plane

| ID | Capability | Implementation | Verification | Evidence and boundary |
|---|---|---|---|---|
| CP-01 | Passive accepted PFCP session lifecycle | Implemented | Recorded lab test | [State reader](../internal/pfcpstate/state.go), [one-call trace](../examples/pcap/one-call/README.md). Establishment, modification and deletion are response-gated; no PFCP termination or injection |
| CP-02 | Rejected/incomplete PFCP and invalid snapshot handling | Implemented | Offline tests only | [PFCP tests](../internal/pfcpstate/state_test.go), [controller tests](../internal/controller/controller_test.go). Includes transactional changes, accepted incomplete-change withdrawal, invalid fields and stale generations; not a full live fault matrix |
| CP-03 | DNN/UE-prefix selection and default ordinary-UPF path | Implemented | Recorded lab test | [Policy tests](../internal/policy/policy_test.go), [baseline](../scripts/test-baseline.sh), [MUP test](../scripts/test-mup.sh), [recorded results](validation-summary.md). Live evidence is the default single-UE profile plus suppression/withdrawal, not simultaneous selected and unselected cohorts |
| CP-04 | SUPI-prefix policy selector | Implemented | Unverified | [Policy evaluator](../internal/policy/policy.go) can use identity extracted by the observer. Requires the relevant PFCP User ID; no SUPI-specific selection run is recorded |
| CP-05 | IPv4 T1/T2 origination, replacement and withdrawal | Implemented | Recorded lab test | [Speaker](../internal/bgp/speaker.go), [wire fixtures](../internal/bgp/wire_compat_test.go), [recorded results](validation-summary.md). T1 carries UE/RAN/DL TEID/QFI; T2 carries UPF/UL TEID and Direct Segment EC. Partial advertisement errors are rolled back locally, not atomically installed on both PEs. Frozen 4.8 fixtures constrain the 4.9 controller; not all MUP encodings are covered |
| CP-06 | ISD/DSD reflection and PE session-route resolution | Implemented | Recorded lab test | [Architecture](architecture.md), [PE provisioning](../ansible/roles/vinbero_pe/tasks/main.yml), [MUP test](../scripts/test-mup.sh), [recorded results](validation-summary.md). T1 resolves against ISD; T2's Direct Segment EC resolves against DSD in this profile |
| CP-07 | Additional ST2 TLVs / EC and nondefault route profiles | Not implemented | Unverified | [Route builder](../internal/bgp/speaker.go), [profile constraints](../scripts/lab-config.py). The selected profile does not integrate additional draft encodings or arbitrary Interwork/Direct choices; upstream parser support is not end-to-end lab support |
| CP-08 | TEID-prefix aggregation | Not implemented | Unverified | [Configuration validation](../scripts/lab-config.py) fixes `mup.teid_prefix_length` to 32. [Wire boundary tests](../internal/bgp/wire_compat_test.go) check decoder lengths, not aggregated forwarding or overlapping-prefix behavior |

## Data plane and deployment profiles

| ID | Capability | Implementation | Verification | Evidence and boundary |
|---|---|---|---|---|
| DP-01 | Selected IPv4 UE traffic over SRv6 in both directions | Implemented | Recorded lab test | [Packet walks](architecture.md), [MUP test](../scripts/test-mup.sh), [sample packets](../examples/pcap/one-call/README.md). Vinbero driver-mode XDP, uplink GTP4 interworking and End.DT4; downlink H.Encaps and End.M.GTP4.E with the [fixed SID layout](address-plan.md) |
| DP-02 | Ordinary UPF forwarding after suppression/withdrawal | Implemented | Recorded lab test | [Baseline](../scripts/test-baseline.sh), [MUP test](../scripts/test-mup.sh), [recorded results](validation-summary.md). Requires a viable original UPF session and kernel routes; withdrawal does not recreate PFCP state or guarantee zero loss |
| DP-03 | Unknown-TEID behavior under injected faults | Partial | Unverified | [Fallback design](architecture.md) passes unmatched traffic to the kernel/UPF path. No dedicated injected unknown-TEID acceptance matrix is recorded, and an invalid TEID need not be accepted by the UPF |
| DP-04 | Multiple QFIs, QFI changes and QoS packet treatment | Partial | Unverified | [Session model](../internal/model/session.go) and [route builder](../internal/bgp/speaker.go) carry one scalar QFI; the [sample](../examples/pcap/one-call/README.md) shows the default QFI. Multi-flow selection, change handling and scheduling/policing are not established by that evidence |
| DP-05 | IPv6 UE / IPv4v6 PDU, AFI 2 and mobile GTP6 profile | Not implemented | Unverified | [Session validation](../internal/model/session.go) requires IPv4 UE and tunnel endpoints; [BGP family](../internal/bgp/speaker.go) is AFI 1. An IPv6 SRv6 underlay does not imply IPv6 UE support |
| DP-06 | Multiple UEs/slices, mobility and subscriber isolation | Partial | Unverified | [Controller](../internal/controller/controller.go) holds multiple session keys, but the [fixture](../config/lab.example.yml) provisions one subscriber/slice. Single-UE re-registration exists; concurrent policy cohorts and gNB/UPF endpoint-change isolation are not verified |
| DP-07 | Multiple segments, collapsed PE and home-routed N9 topologies | Not implemented | Unverified | [Topology](architecture.md) and [configuration](../config/lab.example.yml) provide one Interwork and one Direct attachment with separate PEs, not these additional deployment profiles |

## Tests and observability

| ID | Capability | Implementation | Verification | Evidence and boundary |
|---|---|---|---|---|
| V-01 | One-call deregistration → registration → PDU/data test | Implemented | Recorded lab test | [One-call script](../scripts/test-one-call.sh), [recorded results](validation-summary.md), [sample](../examples/pcap/one-call/README.md). Waits for PFCP-derived routes/maps, 8 ICMP replies, expected HTTP body and increasing XDP counters on both PEs; this is not a voice call |
| V-02 | Observer lease expiry, withdrawal and recovery | Implemented | Recorded lab test | [Lease script](../scripts/test-observer-lease.sh), [recorded results](validation-summary.md). Stops the observer, checks withdrawal/fallback, then restarts with fresh UE/PFCP registration; not controller HA or a bounded zero-loss guarantee |
| V-03 | PE network reconfiguration and neighbor recovery | Implemented | Recorded lab test | [Network recovery script](../scripts/test-network-recovery.sh), [recorded results](validation-summary.md). Exercises netplan apply, systemd-networkd restart and MUP PE (N6/Direct side) DN-neighbor refresh, followed by one-call tests. It is disruptive and opt-in, not a Vinbero/MUP-C daemon restart test |
| V-04 | Independent MUP-C/each-PE daemon restart and loss characterization | Partial | Unverified | [Services and provisioning](../ansible/site.yml) and periodic observer snapshots provide recovery building blocks. The [pending matrix](research-backlog.md) still needs independent restarts, recovered-state comparison and measured loss |
| V-05 | Separate PFCP→BGP, BGP→eBPF and first-packet convergence | Not implemented | Unverified | [Research scope](research-scope.md) requires separate measurements. Polling, ping RTT and merged capture timestamps are not calibrated convergence instrumentation |
| V-06 | Reviewed distributable one-call PCAP walkthrough | Implemented | Recorded lab test | [Seven capture files and review notes](../examples/pcap/one-call/README.md), [digest inventory](../config/public-pcaps.json). N2/N4/BGP/N3/SRv6/N6 plus merged view; lab identifiers/authentication context remain. The exact-file distribution checks are offline checks, not another live call |
| V-07 | Full selected/unselected and fallback comparative capture matrix | Partial | Unverified | [One-call sample](../examples/pcap/one-call/README.md) and [fallback test](../scripts/test-mup.sh) cover parts separately. No complete simultaneous multi-UE cohort and comparative path-capture matrix is provided |
| V-08 | Live topology/status and periodic UE→DN ping display | Implemented | Recorded lab test | [Dashboard implementation](../internal/dashboard/collector.go), [operation](operations.md), [recorded checks](validation-summary.md). Five-second state collection and one active ICMP probe; topology path is inferred from collected state, not per-packet provenance. Historical local and independent Tailnet-peer access was checked; this document does not poll it |

## Reproducibility and operation

| ID | Capability | Implementation | Verification | Evidence and boundary |
|---|---|---|---|---|
| O-01 | Six-VM provisioning and idempotent reapply | Implemented | Recorded lab test | [Portable configuration](portable-configuration.md), [Ansible entry point](../ansible/site.yml), [validation summary](validation-summary.md). Compose is inside the core VM; it does not replace the outer KVM/libvirt topology |
| O-02 | Independent clean OS on the same physical x86-64 host | Implemented | Recorded lab test | [Nested-KVM procedure](clean-room-reproduction.md), [recorded checkpoint](validation-summary.md). The clean OS passed baseline/MUP/lease/one-call and no-change reapply; testing required a temporary original-lab outage |
| O-03 | Complete forwarding reproduction on a second physical host | Partial | Unverified | [Portable settings](portable-configuration.md) and [preflight](../scripts/preflight.sh) exist. The [recorded checkpoint](validation-summary.md) does not establish a second physical machine's full forwarding result |
| O-04 | Native macOS / Apple Silicon ARM64 profile | Not implemented | Unverified | [Preflight](../scripts/preflight.sh) and [VM provisioning](../infra/libvirt/create-vms.sh) require Linux x86-64/KVM. Accessing the dashboard from a Mac is not native lab reproduction |
| O-05 | Fully offline/hermetic OS, kernel and package reproduction | Partial | Unverified | [Locks](../config/versions.lock.yml), [supply-chain limits](supply-chain.md), [common role](../ansible/roles/common/tasks/main.yml). Source/tool/image inputs are pinned; provisioning still fetches dependencies and OS packages, and does not freeze the entire apt/kernel environment |
| O-06 | Controller API authentication/authorization and mTLS | Not implemented | Unverified | [HTTP server](../cmd/mup-controller/main.go) and [controller checks](../internal/controller/controller.go). Trusted-address binding and observer-ID equality checks are not cryptographic authentication or RBAC; use only isolated trusted networks |
| O-07 | Persistent session/suppression state and replicated controller HA | Not implemented | Unverified | [Controller state](../internal/controller/controller.go) is in memory. Operator suppression survives snapshots only within that process lifetime; fresh observation is not persistent replicated state |
| O-08 | Comprehensive resource limits and operational fault hardening | Partial | Unverified | [Server setup](../cmd/mup-controller/main.go) has basic protections such as a header timeout; [pending hardening](research-backlog.md) is not a completed quota, overload or deployment-wide security verification program |
| O-09 | Public-source inventory, bilingual docs and reviewed capture gates | Implemented | Offline tests only | [Export checks](../scripts/export-source.py), [documentation tests](../tests/test_documentation.py), [capture tests](../tests/test_reviewed_pcaps.py), [distribution procedure](source-distribution.md). These validate the source candidate, not a public release, complete dependency security or live packet forwarding |

## Updating this document

- Keep stable row IDs and update the English/Japanese pair together. Check the
  actual code and the exact scenario before changing either status column.
- For a new live claim, record the observation date, revision, locks, kernels,
  commands and results. Add a sanitized summary to [validation evidence](validation-summary.md);
  keep raw host identities, operational logs and unreviewed captures private.
- Unit, syntax, document-hash and packaging checks alone must not promote a
  feature to recorded live verification. A historical pass is not a guarantee
  that a different host or a newly changed runtime currently passes.
- Keep acceptance criteria in [the pending backlog](research-backlog.md). Do
  not mark an entire research area complete because one baseline case passed.
