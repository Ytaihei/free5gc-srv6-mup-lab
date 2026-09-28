# Reproducibility and validation summary

English | [日本語](validation-summary.ja.md)

This is a sanitized summary of recorded lab runs, not a new independent
certification. Raw captures from these historical checkpoints, subscriber
snapshots, host identities, private workflow links and operator approval records
are not distributed. A separately [reviewed one-call PCAP example](../examples/pcap/one-call/README.md)
is available under the exact capture-inventory exception, not as a release of
the underlying private run records.

## Clean-OS reproduction checkpoint

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

## Dependency-maintenance checkpoint

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

For public reports, keep only synthetic lab identities and summarized counts;
review raw dashboard JSON, PCAPs, SSH output and CI artifacts separately.
