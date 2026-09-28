# Hands-on: registration, SRv6 MUP forwarding and source changes

English | [日本語](hands-on.ja.md)

Use this guide to verify the lab yourself, from UE registration to user traffic,
ordinary-UPF fallback, and a small source change. Start with exercises 1–4;
recovery tests and customization are optional. Each exercise includes its
purpose, expected result, dashboard behavior and recovery guidance.

## Scope and safety

- This guide uses the **single-VM compact configuration**, one IPv4 UE and the
  sample address plan. It does not use the six-VM `make bootstrap` or
  `make test-one-call` workflow in the reference documentation.
- Run host commands on Ubuntu 24.04 x86-64 with KVM and a working systemd user
  session. A Mac can be an SSH/browser client, not the native lab host. Commands
  marked as container commands run only after entering the indicated container.
- Source is public, but release images are not distributed. Initial construction
  uses `./lab up --build`. The default VM has 4 vCPUs, 8 GiB RAM and an 80 GiB
  thin-provisioned disk; these are tested settings, not proven minimums. Leave
  host memory and disk headroom and allow network access for dependencies.
- Run one exercise at a time. Tests, startup, shutdown and most rebuilds disrupt
  lab traffic. Do not run manual ping or other traffic concurrently with packet
  evidence tests. Do not use production subscriber data or a production network.
- Keep the original checkout, its private `config/compact.local.yml` and `.lab/`
  ownership records together. A fresh clone does not own an existing VM. Do not
  copy private state into Git, delete ownership records, or adopt somebody else's
  VM to bypass a collision check.
- The commands use default logical addresses and dashboard port `8788`. If you
  changed them, use your own values. Use `./lab --config /absolute/profile.yml`
  before **every** subcommand when using an explicit profile; it is not saved as
  a global CLI selection.

See the [compact setup guide](compact-lab.md) for requirements and configuration,
the [glossary](glossary.md) for terms, and the [architecture](architecture.md) for
control/data-plane roles. The CLI IDs `tpe` and `npe` mean MUP PE (N3/Interwork
side) and MUP PE (N6/Direct side), respectively; they are not specification terms.

## Preparation: choose an existing lab or a new build

For an **existing compact lab**, use the checkout and profile that created it.
Skip cloning and initial construction. If that owned VM is stopped, run
`./lab up`; otherwise proceed directly to exercise 1. Startup reconnects the UE
and may require downloads after a kernel change.

For a **new lab on a prepared, non-conflicting host**, clone the public source:

```bash
git clone https://github.com/Ytaihei/free5gc-srv6-mup-lab.git
cd free5gc-srv6-mup-lab
./lab deps
sudo ./lab deps --apply
```

The first dependency command previews changes; the second applies host package
and group changes. Log out and log back in if group membership changed, return
to this directory, then run:

```bash
./lab doctor
./lab up --build
```

Expected result: `doctor` reports `PASS`, the source build finishes, and startup
reports successful real UE registration and user traffic. Do not continue after
a failed command. If names, addresses, storage or port `8788` conflict, stop and
prepare a separate profile following the setup guide **before** creating a VM.
Do not remove an existing lab to make these defaults fit. Plain `./lab up` on a
fresh clone intentionally stops while the bundled release is unavailable.

Open `http://127.0.0.1:8788/` in a browser **on the lab host**. On a remote client,
that address refers to the client itself, not the host. To use an already
configured Tailnet, explicitly add a Tailnet-only listener on the lab host:

```bash
./lab dashboard start --tailnet
```

Open the URL printed by that command from an authorized Tailnet client. The
dashboard is read-only for configuration but reveals lab/session information to
clients allowed to reach it; it has no application login. Do not expose it to the
public Internet. To remove the extra listener, use `./lab dashboard start --local`.
The six-VM dashboard normally uses `8787`; it is a different lab.

## Exercise 1: check readiness

**Purpose:** distinguish running containers from a ready control and data plane.

Run on the host:

```bash
./lab status
./lab health
```

| Check | Expected result for the default single UE |
| --- | --- |
| Containers | The configured services are running |
| Observer lease | `observer_lease_valid` is `true` |
| BGP peers | `bgp_state` is `2/2 established` |
| PFCP sessions | `observed_sessions` and `selected_sessions` are both 1 |
| MUP session routes | `advertised_routes` is 2: T1 downlink and T2 uplink |
| Health command | Exits successfully with `PASS` |

**Dashboard:** expect a fresh snapshot, a valid lease, two BGP peers, one session,
two session routes and a successful UE-to-DN probe. `health` also checks both PE
APIs and sends a UE ping. Dashboard `/healthz` only checks snapshot freshness:
HTTP 204 there is not proof that the whole lab is healthy.

**Recovery:** if this fails, inspect diagnostics in the troubleshooting section
before starting a disruptive test. Do not treat old green dashboard data as a pass.

## Exercise 2: send traffic yourself

**Purpose:** confirm that packets originate from the UE tunnel rather than a
container's management interface. On the host, enter the UE container:

```bash
./lab shell ue
```

Run **inside the UE container**, then leave it:

```bash
ip address show uesimtun0
ping -I uesimtun0 -c 5 10.210.6.15
curl --interface uesimtun0 --max-time 10 http://10.210.6.15/
exit
```

**Expected result:** the tunnel has a UE address, all five pings receive replies,
and the HTTP body contains `free5GC SRv6 MUP lab data network`. UE addresses can
change after registration; do not hard-code a previously observed UE address.

**Dashboard:** the periodic probe should succeed and PE counters should grow
while the MUP path is active. Animation is inferred from collected state and
probes, not a per-packet trace. Ping success alone does not prove an MUP bypass;
exercises 3 and 4 add packet checks.

**Recovery:** `exit` returns to the host. A missing tunnel or failed request is a
failure to investigate, not a reason to remove the interface option. Stop this
manual traffic before automated tests.

## Exercise 3: run one registration-to-data call

**Purpose:** exercise actual signaling and forwarding, not synthetic sessions.
Keep the dashboard open and run on the host:

```bash
./lab test one-call
```

The test deregisters the UE, waits for PFCP deletion and T1/T2 withdrawal,
registers the UE again, and waits for a new PDU session and PFCP-derived MUP
advertisements. It requires registration/PDU success logs, eight ICMP round
trips, the expected HTTP body, both PE redirect counters, and correlated packet
and PE-specific BPF evidence. For MUP traffic, the checked UE packets must not
appear on the UPF's N3/N6 interfaces.

**Expected result:** successful exit, control/packet evidence messages, and:

```text
PASS: one-call Registration -> PDU -> PFCP -> MUP -> ICMP/HTTP
```

**Dashboard:** sessions/routes disappear and return, ending at one session and
two routes. Its five-second polling can miss short transitions. Compact tests
do not provide the six-VM script's `OBSERVE_SECONDS` pause. During packet checks,
the periodic probe is paused to avoid contaminating evidence; `idle` during that
pause is not a measured packet failure. Read-only collection continues.

**Recovery:** the test attempts to start the UE even if deregistration fails;
this is not an unconditional recovery guarantee. After an error or interruption,
inspect logs and run `./lab health` before continuing. Successful completion
leaves the UE registered and MUP enabled. Here “call” means registration and a
data session, not a voice call.

## Exercise 4: compare MUP with ordinary-UPF fallback

**Purpose:** show that withdrawing MUP routes does not require deleting the
underlying PFCP session. Run on the host:

```bash
./lab test baseline
```

| Phase | MUP session routes | Expected user path |
| --- | ---: | --- |
| Initially selected | 2 | SRv6 MUP |
| Session suppressed | 0 | Ordinary UPF, with the same PFCP session |
| Session resumed | 2 | SRv6 MUP again |

The test checks the same ICMP packets on both UPF interfaces, and their absence
from the SRv6 bridge, during fallback. It then verifies the MUP path again.
`./lab test mup` currently calls the same scenario; there is no need to run both.

**Expected result:** successful exit and:

```text
PASS: ordinary UPF fallback and MUP resume for the same PFCP session
```

**Dashboard:** PFCP remains present while the route count changes and the path
switches. Polling can miss the brief fallback phase. The automated packet
evidence, not animation alone, establishes which path carried the test packets.

**Recovery:** the test attempts to resume the suppressed session on failure.
Check `./lab health` afterwards; consult the diagnostics below if routes remain
withdrawn. Fallback still depends on a working UPF session and routes.

## Exercise 5: test recovery (optional)

**Purpose:** verify specific managed failure/recovery cases. These commands
interrupt lab traffic; choose one at a time on the host.

| Command | Deliberate change and expected result |
| --- | --- |
| `./lab test lease` | Stop the observer, wait for its 15-second lease to expire and MUP routes to withdraw, verify UPF fallback, then restart observation and re-register the real UE |
| `./lab test restart` | Restart both PE containers without changing their images/container IDs, apply managed PE initialization, and check the existing session before a new call |
| `./lab test network` | Recreate only both PE containers and their network namespaces, restore routes/VRF membership, and verify forwarding |
| `./lab test neighbor` | Remove only the dynamic DN neighbor entry on the N6/Direct-side PE and require its periodic process to restore it within 30 seconds |

**Expected result:** the selected command exits successfully and subsequent
`./lab health` passes. The dashboard can temporarily show withdrawn routes,
unavailable peers or an idle/failed probe depending on the scenario, then recover.
An observer that missed existing signaling cannot reconstruct that session just
by restarting; the lease test deliberately generates fresh UE/PFCP signaling.

**Recovery:** after failure, preserve the diagnostics and evidence, then follow
the troubleshooting section. These are bounded, managed recovery tests, not a
guarantee of unattended or lossless recovery from arbitrary crashes.

To run every packet/recovery scenario instead of selecting individual ones:

```bash
./lab test all
./lab health
```

This includes exercises 3–4 and the four scenarios above. It is not a clean build,
a VM-reboot test, a second-physical-host test or a performance benchmark.

## Exercise 6: change the dashboard and roll back (optional)

**Purpose:** verify editable source, a component rebuild and image rollback
without first changing packet-processing logic. On the host:

```bash
./lab source mup-dashboard
```

In the printed source directory, use your editor to change only the visible
heading in `internal/dashboard/web/index.html`:

```html
<h1>SRv6 MUP Observatory</h1>
```

For example, use:

```html
<h1>My SRv6 MUP Lab</h1>
```

Record any pre-existing edits and do not overwrite them. Then, from the host's
checkout, run:

```bash
./lab rebuild mup-dashboard
./lab builds
./lab health
```

**Expected result:** the build succeeds, its record is active, and reloading the
browser shows your heading. The dashboard/collector restart can briefly interrupt
display updates. If needed, force a browser reload to avoid cached assets.

**Recovery and rollback:** after verifying a successful rebuild, restore the
previous dashboard image:

```bash
./lab rollback mup-dashboard
./lab health
```

Reload the browser: the previous heading should return. Rollback restores the
previous image, **not** the host source file or database. Use your editor to undo
only your heading change if you want to leave the source as it was. A future
rebuild otherwise reintroduces the changed heading. Do not discard unrelated
edits or prune images needed for rollback. A compilation failure does not activate
a new image; a failed activation attempts recovery and reports any unfinished
transaction. See the [development workflow](prebuilt-development.md) for limits.

## Inspect results and finish

Run on the host:

```bash
./lab evidence
./lab logs ue
./lab logs observer
./lab logs mupc
```

The evidence command lists saved result summaries, including failed recovery
results where recorded; it does not rerun or approve tests. Check the latest run,
its result and the test's exit status rather than an older success. Raw PCAPs,
BPF dumps and results live **inside the guest** under
`/opt/srv6-mup-compact/.lab/runtime/evidence/`. Some failures have captures/logs
but no completed result summary. Preserve them for diagnosis. New captures,
logs, snapshots and diagnostics are private by default; do not upload them to
GitHub without a separate review. For safe-to-share examples, follow the
[reviewed PCAP walkthrough](../examples/pcap/one-call/README.md).

Leave the lab running for more experiments, or stop your owned compact VM:

```bash
./lab down
```

This stops the dashboard tunnel and gracefully shuts down the VM while retaining
its disk, source and records. Later, from the same checkout/profile, resume it:

```bash
./lab up
./lab health
```

Startup reconnects the UE; it is not a hitless pause/resume. Do not use `destroy`
for ordinary shutdown, and do not delete `.lab/`. Reusing this existing VM is not
a clean reproduction of a newly cloned public revision.

## Troubleshooting and pass criteria

If a command fails, stop the exercise sequence and collect bounded diagnostics:

```bash
./lab diagnose
./lab dashboard status
```

| Symptom | Next step |
| --- | --- |
| First plain startup reports no release | Use `./lab up --build` only for your intended new source-built lab |
| KVM, groups, memory, storage or network checks fail | Resolve the reported prerequisite; reconnect after group changes; do not bypass ownership/collision checks |
| Fresh clone refuses an existing VM | Return to the original owning checkout/profile, or configure a separate non-conflicting VM |
| Browser cannot connect | Verify host versus client loopback, configured port, VM/tunnel state and Tailnet access; do not open a public wildcard listener |
| Probe is `idle` during a packet test | Wait for the test to finish; this avoids interference, and is not a successful ping or an observed loss |
| Snapshot is stale or `/healthz` returns 503 | Inspect collector/tunnel state; do not trust the old topology as current |
| Routes remain withdrawn after an interrupted test | Inspect `./lab logs mupc`, observer/UE logs and diagnostics; after retaining evidence, `./lab up` performs a disruptive coordinated restart/reconnection and attempts pending activation recovery |
| Rebuild/rollback recovery remains incomplete | Keep state and images; follow the development guide rather than deleting transaction records |

After a recovery attempt, run `./lab health`. Do not label a failed exercise a
pass merely because the lab is healthy again; rerun that exercise when safe.
For basic acceptance, exercises 1–4 must pass and health must pass afterwards.
For customization, additionally observe the changed heading and its image
rollback. This guide documents checks against the implementation; adding or
translating it is not evidence of a new live run, broader standards compliance
or second-host reproducibility.
