# Vinbero integration

English | [日本語](README.ja.md)

The lab pins upstream Vinbero `v0.1.1` (`0d7ccf3c798fffa000ccee82e1fd8c9d86525099`).
Ansible applies `patches/0001-gobgp-v4.8-mup-draft01.patch`, regenerates the
embedded eBPF objects from the pinned C source, runs `go mod tidy`, and builds
the daemon/CLI from source. The patch replaces Vinbero's
GoBGP 4.7 fork with upstream GoBGP 4.8 and supplies the explicit Direct
Segment MUP extended-community subtype required by the v4.8 API.

The active MUP controller now selects GoBGP 4.9.0 in the root `go.mod`; the
PE deliberately stays on 4.8.0. This mixed-version baseline requires the live
MUP/withdrawal/lease/one-call gate, as well as the controller's frozen 4.8 UPDATE
fixtures. The shared Go compiler is pinned separately in `config/versions.lock.yml`.

The host-side verification deliberately excludes privileged eBPF runtime
tests. Those tests require `CAP_BPF`, `CAP_SYS_ADMIN`, and memlock changes and
are executed inside the MUP PE (N3/Interwork side)/MUP PE (N6/Direct side) VMs after deployment.

Vinbero is distributed under the Apache License 2.0. The patch in this
directory is a modification against the commit named above. The complete
license text is available in the repository root `LICENSE` file, and the
distribution boundary is recorded in `THIRD_PARTY_NOTICES.md`.

The distributed patch modifies `go.mod`, `pkg/bgp/gobgp/advertise_mup.go`, and
`pkg/bgp/gobgp/decode_mup_test.go`; it does not vendor Vinbero's complete source
or compiled eBPF objects. Source review on 2026-09-07 confirmed that the pinned
tree has LICENSE but no separate NOTICE file. Keep this modification/provenance
record and the root license/notices with any source snapshot. The full patched
runtime tree or binary is a different distribution artifact requiring its own
review.
