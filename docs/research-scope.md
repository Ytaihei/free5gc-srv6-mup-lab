# Research scope and claim boundaries

English | [日本語](research-scope.ja.md)

The implementation separates the following claims so a control-plane success
is not presented as end-to-end MUP forwarding.

| Level | Assertion | Evidence |
|---|---|---|
| L0 | A conventional free5GC PFCP/GTP-U session carries UE traffic through the ordinary UPF | `make test-baseline` with the session suppressed |
| L1 | The passive N4 observer reconstructs accepted PFCP state without manual TEIDs | `mupctl sessions`, observer tests, and live PFCP session values |
| L2 | Policy-selected PFCP state becomes paired T1/T2 MUP SAFI routes and withdrawals | Go tests, `mupctl`, and Vinbero MUP route state |
| L3 | Received MUP routes program Vinbero eBPF entries and selected UL/DL packets bypass the UPF | `MUP_ENABLE=1 make test-mup` plus Vinbero map/state checks |
| L4 | Failure, mobility, and timing behavior is characterized reproducibly | `MUP_ENABLE=1 make test-lease` plus the remaining disruptive matrix and packet captures |

The repository implements L0-L3. A deployment report may claim a level only
after that level's live test has passed on the reported VM set. Local unit and
syntax tests alone prove neither PFCP capture nor packet forwarding.

See [feature status](feature-status.md) for per-capability implementation and
verification columns, and [the glossary](glossary.md) for terminology.

The next research increment is L4:

1. Measure PFCP-to-BGP, BGP-to-eBPF, and first-packet convergence separately.
2. Completed: observer lease expiry/withdrawal/fallback/recovery is automated by `make test-lease`.
3. Restart MUP-C and each PE daemon independently; record packet loss and state
   rebuild. Completed PE network reconfiguration/neighbor recovery tests are
   different from this daemon-failure matrix; see [recorded results](validation-summary.md).
4. Exercise unknown TEID, rejected PFCP response, incomplete modification, QFI
   change, UE re-registration, and gNB/UPF endpoint change. Single-UE
   re-registration is already covered by the [one-call script](../scripts/test-one-call.sh).
   Rejected/incomplete PFCP has unit coverage, not a complete live fault matrix.
5. Add a second selected UE and an explicitly unselected policy cohort.
6. Complete comparative captures showing selected traffic bypasses UPF while
   fallback traffic does not. The [seven one-call PCAP examples](../examples/pcap/one-call/README.md)
   now include N3 access, SRv6 and N6 for the selected-path walkthrough, but do
   not provide the full selected/unselected cohort and fallback capture matrix.

Normative and implementation baselines:

- `draft-ietf-dmm-mup-architecture-02`
- `draft-ietf-bess-mup-safi-01`
- RFC 9433 SRv6 mobile user-plane behaviors
- GoBGP v4.9.0 in MUP-C and v4.8.0 in both Vinbero PEs (intentional
  mixed-version MUP AFI/SAFI interoperability baseline)
- Vinbero v0.1.1 plus the tracked compatibility patch in `third_party/vinbero`

Both MUP documents are Internet-Drafts. Record the exact revisions, GoBGP
version, Vinbero commit, kernel, and test-script revision in every experiment
report.

`go.mod` selects the controller version; the tracked Vinbero patch selects the
PE version. The legacy `gobgp_version` Ansible variable is unused by the
public lab's active roles. The controller's Type 1/2 UPDATE bytes are checked
against frozen 4.8.0 fixtures in `internal/bgp/wire_compat_test.go`. A live MUP,
withdrawal, lease-recovery and one-call gate is still required after changes;
unit fixtures alone do not establish PE forwarding interoperability.

Future implementation areas and acceptance criteria are maintained in
[the research backlog](research-backlog.md). Items in that file
remain planned work and do not expand the claim levels above until their live
tests pass.
