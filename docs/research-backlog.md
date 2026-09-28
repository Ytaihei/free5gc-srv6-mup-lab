# Pending research work

English | [日本語](research-backlog.ja.md)

These experimental extensions remain pending, not delivery commitments.
Some build on existing partial implementations or already tested baseline
cases; the [feature status table](feature-status.md) separates those from
missing features and unverified scenarios. Portability and maintenance take
priority. The implemented profile and exact standards revisions remain in
[research scope](research-scope.md); terminology is in the [glossary](glossary.md).

| Area | Acceptance evidence required before claiming support |
|---|---|
| Convergence and failure recovery | Separately measure PFCP-to-BGP, BGP-to-eBPF and first-packet timing; restart each control/data-plane component and record loss and recovered state |
| Multiple subscribers and mobility | Selected and unselected UEs, endpoint changes and re-registration with no stale or cross-subscriber forwarding |
| IPv6 UE and mobile behaviors | AFI 2 route fixtures, PFCP/address-model support and bidirectional IPv6 packet evidence through the relevant SRv6 behaviors |
| QoS/QFI handling | Accepted PFCP updates produce the intended session route and packet treatment; QFI changes and unsupported combinations have explicit tests |
| Extended MUP route/segment profiles | Validate each additional profile against its pinned draft/RFC revision with independent wire fixtures and PE forwarding tests |
| Operational hardening | Document trusted interfaces; test authentication/authorization, diagnostic redaction and resource limits before any broader deployment claim |

Unit tests are necessary but do not establish forwarding, failover or standards
conformance. Preserve a conventional UPF fallback for disruptive experiments.
