# Lab glossary

English | [日本語](glossary.ja.md)

Start here for abbreviations used by this repository. These are short learning
notes, not replacements for the specifications. The final column describes this
lab, not every possible MUP deployment. See [feature status](feature-status.md)
for implementation and verification limits and [architecture](architecture.md)
for packet walks. Reviewed: 2026-09-15; the pinned standards baseline has not changed.

## 5G nodes and identities

| Term | Meaning | In this lab |
|---|---|---|
| UE | User Equipment; the mobile endpoint | UERANSIM simulates one subscriber; its traffic interface is `uesimtun0` |
| gNB / RAN | 5G base station / Radio Access Network | UERANSIM simulates the gNB and UE in `lab-ran`; no physical radio is required |
| AMF | Access and Mobility Management Function | free5GC handles UE registration and access/mobility signaling |
| SMF | Session Management Function | free5GC controls the ordinary UPF through N4/PFCP |
| UPF | User Plane Function | Remains present for PFCP session control and ordinary fallback forwarding; MUP bypass does not remove it |
| DN / DNN | Data Network / its identifying name | `lab-dn` is the traffic endpoint; `internet` is the configured DNN, not proof of public Internet access |
| PDU Session | Protocol Data Unit session; a UE's data connectivity | Registration and data-session establishment are separate steps in the one-call test |
| SUPI / IMSI | Permanent subscriber identity / one form of it | Policy can match SUPI prefixes when PFCP supplies the identity; a UE IP is not a SUPI |
| SUCI | Concealed form of the subscriber identity | The sample uses a test subscriber and NULL protection scheme; do not treat it as anonymous production data |
| PLMN | Public Land Mobile Network identifier, formed from MCC and MNC | Public fixture identifiers describe the simulated network, not a real operator connection |
| S-NSSAI | Single Network Slice Selection Assistance Information | The fixture selects one slice; this is not multi-slice validation |
| UL / DL | Uplink / downlink | UE to DN / DN to UE; direction is relative to the UE, not a PE interface |

For mobile-network context see [RFC 9433 §2.1](https://www.rfc-editor.org/rfc/rfc9433.html#section-2.1)
and its 3GPP references. The actual fixture and modeled fields are in
[lab configuration](../config/lab.example.yml) and the [session model](../internal/model/session.go).

## Interfaces and session protocols

| Term | Meaning | In this lab |
|---|---|---|
| N2 / NGAP / SCTP | gNB–AMF control interface, its application protocol and transport | Carries registration-related signaling and PDU resource setup; see the N2 sample PCAP |
| N3 / GTP-U | gNB–UPF user interface / tunneled user packets | The N3 access/core split places MUP PE (N3/Interwork side) in the IPv4 GTP-U path |
| N4 / PFCP | SMF–UPF control interface / Packet Forwarding Control Protocol | The observer snoops both directions; it neither terminates PFCP nor sends session requests |
| N6 / N9 | UPF–DN / UPF–UPF user interfaces | N6 is deployed; multi-UPF N9 roaming is not a provided topology |
| TEID / F-TEID | 32-bit tunnel endpoint identifier / endpoint-qualified tunnel information | Pair the TEID with the GTP endpoint address and direction; the number alone is not globally unique |
| SEID / F-SEID | 64-bit PFCP session endpoint identifier / its address-qualified form | CP and UP SEIDs track the PFCP session; they are not the UL/DL TEIDs |
| PDR / PDI | Packet Detection Rule / Packet Detection Information | Observer inputs describing packet matching, UE address, DNN and tunnel information |
| FAR | Forwarding Action Rule | Observer extracts the RAN-side forwarding endpoint and downlink TEID |
| QER / QFI | QoS Enforcement Rule / QoS Flow Identifier | Selected QFI is carried in the session and T1; that alone does not implement QoS scheduling or policing |
| QoS | Quality of Service; differentiated treatment of traffic | A QFI label describes a flow; this lab does not provide a complete per-flow service guarantee |
| Snapshot / observer lease | Full observed-state report / freshness deadline | Reports follow changes and repeat every 5 seconds; 15-second expiry triggers route withdrawal, not PFCP deletion |

The implemented PFCP subset is defined by the [passive state reader](../internal/pfcpstate/state.go),
not by all fields the upstream PFCP package can decode. See
[architecture roles](architecture.md#roles) and [operations](operations.md).

## MUP and BGP routes

| Term | Meaning | In this lab |
|---|---|---|
| MUP / SRv6 MUP | Mobile User Plane architecture / its SRv6 data-plane realization | Session state becomes routing information; the supported profile is IPv4 UE with a Direct Segment |
| MUP-C | MUP controller | `lab-mupc` applies policy and originates session routes; its observer runs separately in `lab-core` |
| MUP PE | MUP-aware provider edge | Both Vinbero edges are MUP PEs: MUP PE (N3/Interwork side), ID `tpe`, and MUP PE (N6/Direct side), ID `npe` |
| T-PE / N-PE | Legacy lab aliases, not MUP specification terms | Use the MUP PE labels above in explanations. Keep `tpe`/`npe` and `lab-tpe`/`lab-npe` only as compatible configuration, API, CLI and VM identifiers |
| MUP-GW | Historical gateway term in an early MUP draft | Not the current display name for the N3-side edge; use MUP PE (N3/Interwork side). It is not another SMF or PFCP endpoint |
| Direct / Interwork Segment | Direct attachment of a network/service / interworking with a mobile user-plane protocol | This lab attaches N6/DN as Direct and N3/GTP-U as Interwork. Interwork can also accommodate N9; one MUP PE can host both segment types. A MUP segment is not the same concept as a Segment Routing segment |
| ISD / DSD | Interwork / Direct Segment Discovery route | MUP PE (N3/Interwork side) and MUP PE (N6/Direct side) advertise discovery information used to resolve the session routes |
| T1 / T1ST / ST1 | Type 1 Session Transformed route | Carries UE prefix, RAN endpoint, DL TEID and QFI; MUP PE (N6/Direct side) uses it for downlink |
| T2 / T2ST / ST2 | Type 2 Session Transformed route | Carries UPF endpoint and UL TEID; MUP PE (N3/Interwork side) resolves its Direct Segment for uplink |
| BGP / iBGP / RR | Routing protocol / same-AS peering / route reflector | MUP-C reflects discovery routes between the two PEs as well as originating T1/T2 |
| AFI / SAFI | Address Family Identifier / Subsequent Address Family Identifier | Session routes use AFI 1 and SAFI 85. AFI 1 does not mean the SRv6 underlay is IPv4 |
| NLRI | Network Layer Reachability Information | The typed route payload inside BGP; a session-route update is not a user packet |
| RD / RT | Route Distinguisher / Route Target | RD distinguishes routes; RT controls import scope. They are different fields even if both look like `65000:100` |
| EC / TLV | Extended Community / type-length-value field | RT and a Direct Segment EC are used; extra draft TLVs/EC profiles are not automatically supported by this lab |

The naming follows [MUP Architecture Draft §2–4.1](https://datatracker.ietf.org/doc/html/draft-ietf-dmm-mup-architecture-02#section-2).
The [early draft's MUP-GW definition](https://datatracker.ietf.org/doc/html/draft-mhkk-dmm-srv6mup-architecture-02#section-2)
is retained here only to explain historical material, not as another current PE type.

The four route types are easy to confuse with their names:

| Wire Route Type | Route |
|---|---|
| 1 | ISD |
| 2 | DSD |
| 3 | T1 / ST1 |
| 4 | T2 / ST2 |

Architecture Type 1 (3GPP-5G) is a separate field. Thus a T1 route does **not**
have Route Type 1. Refer to the pinned
[MUP architecture draft](https://datatracker.ietf.org/doc/html/draft-ietf-dmm-mup-architecture-02),
[MUP SAFI draft §3.1](https://datatracker.ietf.org/doc/html/draft-ietf-bess-mup-safi-01#section-3.1),
[multiprotocol BGP](https://www.rfc-editor.org/rfc/rfc4760.html), and
[RD/RT definitions](https://www.rfc-editor.org/rfc/rfc4364.html).
The lab mapping is in [address planning](address-plan.md) and the
[route builder](../internal/bgp/speaker.go).

## SRv6 and packet forwarding

| Term | Meaning | In this lab |
|---|---|---|
| SRv6 / SID | Segment Routing over IPv6 / a segment identifier encoded as an IPv6 address | The outer transport is IPv6 even though the UE payload is IPv4 |
| Locator / function / arguments | Routable SID prefix / local action / action parameters | This profile has a fixed SID layout; session arguments can encode RAN address, TEID and QFI |
| SRH | Segment Routing Header, IPv6 Routing Type 4 | Visible in the SRv6 sample; not every possible SRv6 encapsulation is required to include the same header layout |
| H.Encaps | Headend encapsulation behavior | Adds the outer IPv6/SRv6 envelope to selected inner traffic |
| End.DT4 / VRF | IPv4 decapsulation-and-table-lookup behavior / a routing context | MUP PE (N6/Direct side) decapsulates uplink traffic into tenant routing table 100 |
| End.M.GTP4.E | Mobile behavior that emits IPv4 GTP-U | MUP PE (N3/Interwork side) reconstructs downlink GTP-U toward the gNB; this is not the uplink encapsulation behavior |
| Args.Mob.Session | Mobile-session arguments carried in the SID layout | The deployed profile uses a fixed offset; it is not a general-purpose arbitrary SID-layout decoder |
| eBPF / XDP | In-kernel programs/maps / early receive-path processing | Vinbero uses driver-mode XDP on the PE virtio interfaces |
| Fallback | Ordinary UPF forwarding when no selected MUP entry applies | Requires the underlying UPF session and kernel routes to remain valid; it is not a guarantee of zero loss |

See [RFC 8754](https://www.rfc-editor.org/rfc/rfc8754.html),
[RFC 8986](https://www.rfc-editor.org/rfc/rfc8986.html), and
[RFC 9433](https://www.rfc-editor.org/rfc/rfc9433.html) for the respective header,
network-programming and mobile-behavior definitions. The actual packet walks
and fixed layout are in [architecture](architecture.md) and [address planning](address-plan.md).

## Tools, tests and distribution

| Term | Meaning | In this lab |
|---|---|---|
| free5gc-compose / UERANSIM | Containerized 5G core / simulated UE and gNB | Compose runs inside core VM; the complete lab also needs KVM/libvirt and Ansible |
| GoBGP / Vinbero | BGP library / MUP SRv6 implementation using eBPF | Controller and PEs intentionally use different pinned GoBGP versions; library capability is not a lab support claim |
| Connect RPC / mupctl | Typed HTTP RPC interface / controller CLI | Status reads and suppression mutations are different operations; trusted-network binding is not API authentication |
| 1call / E2E | This lab's single registration-to-data test / end-to-end test | Includes a PDU Session and ICMP/HTTP, not a voice call or an IMS/VoNR test |
| RTT / convergence | Packet round-trip time / time for state and forwarding to settle | Dashboard ping RTT does not measure PFCP-to-BGP or BGP-to-eBPF convergence |
| PCAP / PCAPNG | Packet capture file formats | Only seven explicitly reviewed classic PCAP samples are admitted; ordinary captures remain private |
| Fixture / golden wire fixture | Fixed test input / expected protocol bytes | Useful for regression checks, not independent full RFC conformance certification |
| CI / SBOM | Continuous integration / software bill of materials | Offline checks and dependency inventories do not replace privileged live forwarding tests |
| Idempotence / clean-OS reproduction | No-change reapply / rebuild from a fresh OS | An unchanged Ansible recap and a new installation are different kinds of evidence |
| Pending / unverified | Deferred work / absence of matching verification evidence | Neither means the same thing as no implementation; see the two status columns in the feature table |

Continue with [operations](operations.md), [feature status](feature-status.md),
and the [reviewed PCAP walkthrough](../examples/pcap/one-call/README.md).
