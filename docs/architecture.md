# SRv6 MUP architecture

English | [日本語](architecture.ja.md)

Use the [glossary](glossary.md) for abbreviations and [feature status](feature-status.md)
for implementation limits and the corresponding verification evidence.

Both edges are MUP PEs, labelled **MUP PE (N3/Interwork side)** and
**MUP PE (N6/Direct side)**. Interwork/Direct classify the segments they
accommodate, not two mandatory PE device types. A single MUP PE can accommodate
both kinds; the N3/N6 pairing describes this lab, not every MUP deployment.
See [MUP Architecture Draft §3–4.1](https://datatracker.ietf.org/doc/html/draft-ietf-dmm-mup-architecture-02#section-3).
Configuration keys, Compose/API IDs `tpe`/`npe`, VM names `lab-tpe`/`lab-npe`,
and existing command/environment-variable names remain unchanged for compatibility.

## Roles

- `lab-core`: free5GC control plane and ordinary UPF. `pfcp-observer` passively
  reads bidirectional N4 traffic from Docker bridge `br-free5gc`; it does not
  terminate PFCP or modify the SMF/UPF exchange.
- `lab-mupc`: policy engine, BGP MUP speaker, and route reflector. It receives
  full session snapshots over Connect RPC, originates/withdraws T1/T2 routes,
  and reflects MUP PE (N3/Interwork side) ISD/MUP PE (N6/Direct side) DSD discovery routes between the packet edges.
- `lab-tpe`: MUP PE (N3/Interwork side). Vinbero matches selected uplink F-TEIDs and
  translates GTP-U to SRv6; its local ISD advertises `End.M.GTP4.E` for the
  reverse direction.
- `lab-npe`: MUP PE (N6/Direct side). Vinbero terminates selected uplink SRv6 at
  `End.DT4`, and encapsulates selected UE downlink traffic toward the MUP PE (N3/Interwork side); its
  local DSD binds Direct Segment `65000:1` to the `End.DT4` SID.
- `lab-ran` / `lab-dn`: UERANSIM gNB+UE and the data-network test endpoint.

## Control-plane sequence

1. The SMF sends an N4 PFCP session request to the UPF.
2. The observer stages the request and commits its state only after a matching
   accepted response. It reconstructs the UE address, DNN, CP/UP SEIDs, UPF and
   RAN F-TEIDs, and QFI. An incomplete accepted modification fails closed.
3. The observer immediately sends a full snapshot after a state change and
   repeats it every 5 seconds. Snapshots have a monotonic generation number.
4. MUP-C selects sessions matching the initial policy (`DNN=internet`, UE in
   `10.60.0.0/16`) and advertises T1 and T2 as a transactional pair.
   The same in-process GoBGP instance reflects ISD/DSD between its two iBGP
   clients, so no parallel packet-edge BGP session is required.
   Pairing includes local rollback on partial advertisement failure, not
   simultaneous atomic installation on both PEs.
5. The MUP PE (N6/Direct side) imports T1 (RT `65000:100`) and resolves it against the MUP PE (N3/Interwork side) ISD,
   creating the per-UE downlink headend. The MUP PE (N3/Interwork side)'s End.M.GTP4.E uses the
   configured N3-core address `10.210.32.10` as the reconstructed GTP-U source.
6. The MUP PE (N3/Interwork side) imports T2 and resolves Direct Segment `65000:1` against the MUP PE (N6/Direct side)
   DSD, creating the uplink endpoint gate and exact F-TEID entry.
7. PFCP deletion, operator suppression, session changes, or a 15-second
   observer lease expiry withdraws the pair. Unknown/unselected traffic is not
   captured by a Vinbero MUP entry and therefore follows the kernel/UPF route.

## Selected uplink packet walk

```text
UE -> gNB
   GTP-U {dst=10.210.32.10, TEID=UL}, routed through MUP PE (N3/Interwork side) 10.210.31.12
-> MUP PE (N3/Interwork side) Vinbero T2 match
   SRv6 H.Encaps {segment=fd10:2:0:1::}
-> MUP PE (N6/Direct side) End.DT4, table 100
-> N6 -> DN
```

The MUP PE (N3/Interwork side) also has `10.210.32.12` on N3 core. When no exact T2 entry exists,
Vinbero passes the packet and the kernel routes it to ordinary UPF
`10.210.32.10`.

## Selected downlink packet walk

```text
DN -> route 10.60.0.0/16 via MUP PE (N6/Direct side)
   IPv4 {dst=UE}
-> MUP PE (N6/Direct side) Vinbero T1 match
   SRv6 H.Encaps {segment=ISD SID + RAN IPv4/DL TEID/QFI args}
   source fd10:2::
-> MUP PE (N3/Interwork side) End.M.GTP4.E
   uses fixed GTP-U source 10.210.32.10 and Args.Mob.Session at byte 7
-> GTP-U -> gNB -> UE
```

If T1 is absent, the MUP PE (N6/Direct side)'s kernel route sends the UE prefix to ordinary UPF
`10.210.6.10`. Thus route withdrawal restores the original free5GC data path
without changing the PFCP session.

## Failure semantics

- PFCP requests are never published before an accepted response.
- Stale snapshot generations and unexpected observer IDs are rejected.
- T1/T2 partial advertisement is rolled back.
- Changed F-TEIDs replace both paths transactionally.
- Observer loss expires after 15 seconds and withdraws all advertised sessions.
- Operator suppression is per session and persists across observer snapshots
  for the life of the MUP-C process.
- Vinbero and MUP-C APIs bind only to lab/loopback addresses; no MUP service is
  exposed on the physical LAN.
- PE packet programs attach in XDP driver mode. This is required on the lab's
  virtio NICs so cross-interface redirects are flushed to the egress device.

## Observability plane

`mup-dashboard` runs on the virtualization host and is deliberately outside the
MUP control path. Every five seconds it collects configuration state and sends
one active U-Plane ICMP probe:

```text
MUP-C Connect API ── status + controlled PFCP sessions ──┐
lab-core/RAN/PE/DN ── SSH systemd active state ──────────┤
MUP PEs (both sides) ─ SSH Vinbero JSON routes/counters ─┤
UE uesimtun0 ─────── ICMP echo to DN 10.210.6.15 ───────┤
                                                         v
                                               immutable JSON snapshot
                                                         |
                                     loopback + tailscale0 HTTP dashboard
```

The UI derives the highlighted MUP path only when the observer lease is valid,
at least one controlled session is advertised, and the T1/T2 route pair is
present. Otherwise it highlights the ordinary UPF fallback path. A successful
probe animates its request and reply on that path and reports the RTT; a timeout
marks the U-Plane red. Collection does not call `suppress`, `resume`,
`reconcile`, or any Vinbero mutation API.
VM or service failures remain visible in the most recent snapshot rather than
making the dashboard process disappear.
