# SRv6 MUP one-call packet capture example

English | [日本語](README.ja.md)

These are unmodified packets from an isolated lab one-call test, not production
traffic or generated protocol mockups. The selected files have been reviewed
for inclusion in the distribution candidate. This does not publish the repository
or authorize distributing other captures. The repository [LICENSE](../../../LICENSE)
applies to these project-produced sample files and documentation.

## Files and observation points

Start with [00-one-call-all.pcap](00-one-call-all.pcap): 234 frames, 32166 bytes.
It is a chronological merge of the six files below, retaining their packet bytes
and timestamps. It has no interface identifiers: the same inner user packet is
seen at multiple links, so use individual captures for counts, retransmissions
or RTT analysis.

| File | Frames | What to inspect |
|---|---:|---|
| [01-n2-ngap.pcap](01-n2-ngap.pcap) | 20 | Initial Registration, authentication/security exchange, PDU Session Resource Setup |
| [02-n4-pfcp.pcap](02-n4-pfcp.pcap) | 12 | Old session deletion, new session establishment/modification and accepted responses |
| [03-bgp-mup.pcap](03-bgp-mup.pcap) | 76 | Old T1/T2 withdrawal, new AFI 1 / SAFI 85 T1/T2 advertisements to both PEs |
| [04-n3-gtpu.pcap](04-n3-gtpu.pcap) | 42 | GTP-U and inner UE–DN traffic |
| [05-srv6.pcap](05-srv6.pcap) | 42 | SRH Type 4 and inner IPv4 on the PE–PE link |
| [06-n6.pcap](06-n6.pcap) | 42 | Plain IPv4, eight successful test pings and HTTP 200 |

All seven files total 64452 bytes. Expected sizes, packet counts and SHA256
digests are fixed in [the capture inventory](../../../config/public-pcaps.json).

## Follow one call

The existing `ONE_CALL_ENABLE=1 make test-one-call` test completed in 40 seconds.
Captures span 2026-09-11 15:59:16–15:59:56 UTC; packets are not time-shifted.
The old UE address is `10.60.0.22`; the newly registered UE is `10.60.0.23`.
DN is `10.210.6.15`, UL TEID is `90`, DL TEID is `2`, QFI is `1`,
and RD is `65000:10`. New T1/T2 advertisements are logical routes sent to both PEs.

Follow N4 frames 3–4 and BGP frames 14–16 for teardown; N2 frame 5 for
Registration request and frames 6–9 for authentication/security; N4 frames 5–8
and N2 frames 15/17 for PDU setup; BGP frames 33/35–37 for advertisements;
SRv6 frames 9–26 for the eight test pings; N6 frames 30/32 for HTTP GET/200.
Periodic dashboard pings and traffic from the preceding session are also present.

```text
ngap || nas-5gs
pfcp.msg_type >= 50 && pfcp.msg_type <= 55
bgp.update.path_attribute.mp_reach_nlri.safi == 85 || bgp.update.path_attribute.mp_unreach_nlri.safi == 85
ipv6.routing.type == 4 && ip.addr == 10.60.0.23
icmp.ident == 0x59fc
http
```

## Retained information and review limits

Retained intentionally: lab IPs, locally administered virtual MACs, timestamps,
test subscriber `imsi-208930000000001` (PLMN `208/93`, MSIN `0000000001`),
test cell identifiers, authentication exchanges, lab-derived NGAP security-key
material, temporary session identifiers,
TEIDs/SIDs, sample PFCP names `smf.free5gc.org` / `upf.free5gc.org`, DNN `internet`,
and HTTP server/version/date headers. These are fixture values, not a connection
to a real mobile subscriber or the named operator. HTTP body is the fixed lab
page; there is no user browsing session. Authentication material in a lab trace
must never be reused for production credentials.

An offline TShark 4.6.4 review checked decoded address scope, virtual MACs,
subscriber/session context and application traffic. Known private identities
were checked in raw bytes, decoded output and packed IP representations: no
matches. There were no observed non-lab network endpoints. This is not complete
anonymization or proof that arbitrary future captures are safe. NAS-protected
messages remain protected; no decryption key file is included.

The files use classic Ethernet PCAP, not PCAPNG with interface/name-resolution
or decryption-secret metadata blocks. Original frames are preserved; timestamps,
protocol identifiers and HTTP metadata have not been scrubbed. Private capture
scripts, host interface mappings, operational logs and dashboards are excluded.

## Decoder limitations

Wireshark 4.6.4 flags four T1 routes with Source Address Length zero; the lab's
[Draft-01 reference](https://datatracker.ietf.org/doc/html/draft-ietf-bess-mup-safi-01#section-3.1.3.1)
permits zero. Its decoder can stop before the second withdrawn NLRI in BGP frame
14. For T2, compare raw TEID bytes `00 00 00 5a` with PFCP/GTP-U value `90`:
the scalar TEID field differs from the formatted display in that decoder version.
These observations do not establish behavior in every Wireshark version.

SCTP/BGP connections predate capture; initial handshakes are absent. N2 frame 15
has a retransmission indication. Protected Registration Accept/Complete messages
are not all readable as plaintext; the original harness also checked UE logs.
Capture clocks were NTP-synchronized but not calibrated for sub-millisecond
cross-host convergence measurements.

## Verify and add samples

From the repository root:

```bash
make check-public-source
```

CI verifies exact reviewed bytes, classic PCAP structure and packet counts without
requiring Wireshark. The private final review also supplies the external identity
denylist described in [source distribution](../../../docs/source-distribution.md).
Hashes freeze reviewed data; changing a hash does not constitute a new privacy review.

New/replaced samples require packet-level review, bilingual explanation, a new
explicit capture-inventory entry, public-source inventory entry and exact ignore
exception, followed by reviewed PR/CI. Unlisted PCAP/PCAPNG files stay ignored;
even forced additions cannot pass export just by entering the source allowlist.
