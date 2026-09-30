# Reproducibility and validation summary

English | [日本語](validation-summary.ja.md)

This is a sanitized summary of recorded lab runs, not a new independent
certification. Raw captures from these historical checkpoints, subscriber
snapshots, host identities, private workflow links and operator approval records
are not distributed. A separately [reviewed one-call PCAP example](../examples/pcap/one-call/README.md)
is available under the exact capture-inventory exception, not as a release of
the underlying private run records.

The reference checkpoints below are retained as historical evidence. Compact
checkpoints summarize later records separately; this 2026-10-01 documentation
update did not rerun live tests or promote unperformed checks to passes.

<a id="clean-os-reproduction-checkpoint"></a>

## Six-VM reference clean-OS reproduction checkpoint

A September 2026 run created an independent Ubuntu 24.04 environment using
nested KVM on a Linux x86-64 host, then provisioned the six lab guests from the
locked sources and container digests. It passed conventional UPF traffic, MUP
UL/DL forwarding, withdrawal fallback, observer lease recovery, and a complete
UE registration-to-data call. A repeat provisioning run reported no changes.

This establishes clean-OS reproduction on the tested hardware, not portability
to every physical machine. Full forwarding reproduction on a second physical
host and native Apple Silicon/ARM64 execution have not been established. See
[the clean-room procedure](clean-room-reproduction.md) for prerequisites,
resource requirements and the temporary-outage boundary.

<a id="dependency-maintenance-checkpoint"></a>

## Six-VM reference dependency-maintenance checkpoint

The 2026-09-10 maintenance run used Go 1.26.8, PFCP 1.1.2, GoBGP 4.9.0 in
MUP-C and patched GoBGP 4.8.0 in both Vinbero PEs. Source locks and checksums
are in `config/versions.lock.yml`, `go.mod`, `go.sum` and the Vinbero patch.

Recorded checks passed:

- Unit/syntax/vet, selected Go race tests, ShellCheck and source dependency gates.
- MUP UL/DL forwarding, paired withdrawal/fallback and observer lease recovery.
- Four complete UE calls with 8/8 ICMP replies, HTTP traffic and increasing XDP
  redirect counters on both PEs.
- PE network reconfiguration recovery and a repeat six-guest Ansible run with
  zero changed, failed or unreachable tasks.
- Dashboard reads from loopback and an independent Linux Tailnet peer, with
  listeners restricted to loopback and Tailnet IPv4.

This was an update of the running lab, not another clean-OS rebuild. Packaging
or documentation checks do not repeat these live forwarding results. Rerun
the appropriate gates after runtime changes and record the exact revision,
kernel versions, locks, commands and observation date for every new deployment.

## Compact source-build checkpoints

| Recorded date (JST) | Scenario and result | Boundary and detailed record |
|---|---|---|
| 2026-09-20 | Two fresh child VMs on a clean nested-KVM parent passed one-call, fallback, lease and PE recovery tests before/after restart | Same physical host; fresh guest sources/build caches, not two independent physical machines. [Dated compact history](compact-lab.md) |
| 2026-09-23 | A fresh 4-vCPU/8-GiB guest built the common runtime and all eleven clean NFs; registration, correlated ICMP/HTTP captures and the full suite passed before/after restart. Dashboard/SMF rebuild and rollback passed | Only the checksum-verified cloud base was reused; application images, source trees and build caches were not copied. Not byte-identical OCI reproduction. [Initial-build acceptance](compact-lab.md) |
| 2026-09-23–24 | Explicit MongoDB 4.4-to-8.0 migration passed application checks and real registration/traffic; subsequent packet/recovery checks passed | Existing data migrated explicitly, not by silently changing its image lock. Not a new six-VM DB baseline or a general downgrade guarantee. [DB evidence](database-migration.md), [follow-up acceptance](compact-lab.md) |
| 2026-09-24 | Another fresh candidate guest passed source startup, MUP-C rebuild, one-call, rollback and the full packet/recovery suite | The following image audit completed but failed distribution policy, so the candidate command correctly exited nonzero. Not a successful registry-release or GitHub-runner test. [Candidate record](prebuilt-development.md) |

The full compact suite covers one-call, baseline/MUP comparison, lease,
managed PE restart, network and neighbor recovery. These successes do not
complete independent MUP-C/PE daemon restart coverage, quantified loss or
calibrated convergence measurements. HTTP/API checks also do not substitute
for visual dashboard acceptance. Later image-review findings and the
explicit-release dashboard socket-access defect remain in
[image distribution](image-distribution.md); these source-build records do not
approve prebuilt distribution.

## Acceptance boundary and future records

A clean build on a different physical machine remains **unverified**. The
operator excluded it from this acceptance scope; it is not a remaining mandatory
source-publication gate and must not be reported as passed. Native ARM64/macOS,
all nondefault configurations and fully offline/byte-identical builds are not
established by these records. The [research backlog](research-backlog.md) remains pending.

For public reports, keep only synthetic lab identities and summarized counts;
review raw dashboard JSON, PCAPs, SSH output and CI artifacts separately.
