# Address and MUP identifier plan

English | [日本語](address-plan.ja.md)

## Networks

| Segment | Prefix | Endpoints |
|---|---|---|
| Management/BGP | `192.168.123.0/24` | core `.10`, RAN `.11`, MUP PE (N3/Interwork side) `.12`, MUP PE (N6/Direct side) `.13`, MUP-C `.14`, DN `.15` |
| N2 | `10.210.2.0/24` | AMF `.10`, gNB `.11` |
| N3 access | `10.210.31.0/24` | gNB `.11`, MUP PE (N3/Interwork side) `.12` |
| N3 core | `10.210.32.0/24` | UPF `.10`, MUP PE (N3/Interwork side) `.12` |
| SRv6 underlay | `2001:db8:100:10::/64` | MUP PE (N3/Interwork side) `::12`, MUP PE (N6/Direct side) `::13` |
| N6 | `10.210.6.0/24` | UPF `.10`, MUP PE (N6/Direct side) `.13`, DN `.15` |
| UE pool | `10.60.0.0/16` | first test UE `10.60.0.1` |

Every user-plane bridge is isolated from the physical LAN. The host networks,
Docker defaults outside the core VM, and Tailscale addresses are not reused.

## SRv6 and BGP MUP values

| Object | Value |
|---|---|
| MUP PE (N3/Interwork side) locator | `fd10:1::/48` |
| MUP PE (N3/Interwork side) Interwork Segment SID | `fd10:1:0:1::` |
| MUP PE (N3/Interwork side) `End.M.GTP4.E` trigger | `fd10:1::/56` |
| Args.Mob.Session offset | byte `7` |
| IPv4 source extraction position for embedding mode | bit `64`; the baseline uses a fixed GTP-U source instead |
| MUP PE (N3/Interwork side) ISD RD / RT | `65000:12` / `65000:100` |
| MUP PE (N6/Direct side) locator | `fd10:2::/48` |
| MUP PE (N6/Direct side) Direct Segment SID | `fd10:2:0:1::/128` (`End.DT4`) |
| MUP PE (N6/Direct side) DSD originator | `10.210.255.13/32` |
| MUP PE (N6/Direct side) DSD RD / RT | `65000:13` / `65000:200` |
| Direct Segment extended community | `65000:1` |
| Conditional downlink source-embedding prefix (configuration) | `fd10:2:100::/64` |
| Downlink outer source observed in the reviewed PCAP | `fd10:2::` |
| Fixed reconstructed GTP-U source at the N3-side PE | `10.210.32.10` |
| Session RD | `65000:10` |
| T1 route target | `65000:100` |
| T2 route target | `65000:200` |
| T2 endpoint-address length | `64` bits (IPv4 endpoint + exact 32-bit TEID) |

The configured `mup_gtp4_source_prefix` is not an unconditional outer source.
In the pinned Vinbero implementation, embedding requires a matching VRF binding
and a same-RD T2 whose TEID prefix covers the T1 downlink TEID. Without that
anchor the headend keeps its locator-derived source. The
[reviewed one-call capture](../examples/pcap/one-call/README.md) has UL TEID `90`
and DL TEID `2`, so the exact-match T2 does not satisfy that condition; its
downlink outer source is `fd10:2::`, as in the [packet walk](architecture.md).
The baseline N3-side PE uses a fixed reconstructed GTP-U source `10.210.32.10`,
not extraction of an IPv4 address from that outer IPv6 source. Do not infer
source embedding from the configured prefix alone, or silently rewrite the
reviewed capture to match a configuration label.

The T1 route carries the UE `/32`, RAN endpoint, downlink TEID, and QFI. The T2
route carries the UPF endpoint, uplink TEID, and Direct Segment extended
community. The T1/T2 routes intentionally carry no fixed service SID: each PE
resolves the proper SID from the ISD/DSD route in the matching route-target
scope.
