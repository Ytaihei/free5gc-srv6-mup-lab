# Single-VM lab migration

English | [日本語](compact-lab.ja.md)

## Status and release boundary

This is an **experimental implementation**, not the new default or a published
appliance. The six-VM reference remains unchanged. Source is public; runtime
images are not distributed. Initial creation requires `./lab up --build`;
subsequent `./lab up` reuses that VM's local candidate without rebuilding
application binaries. This does not imply reviewed release images exist.

For a step-by-step introduction with expected results and recovery guidance,
start with the [hands-on guide](hands-on.md). This document provides the detailed
setup/reference material and dated validation history.

The [prebuilt/development workflow](prebuilt-development.md) describes the
implemented digest-pinned release loader, on-demand builder and CI boundaries.
The bundled release manifest remains empty until distribution approval.

The goal is one Ubuntu x86-64 KVM VM containing a Compose-defined 5G/MUP lab,
with prebuilt runtime images and component-level source customization. The
first compatibility gate uses Ubuntu 24.04, kernel 6.8, pinned gtp5g, and
Vinbero generic XDP on veth. Performance equivalence to the reference's virtio
driver-mode XDP is not claimed. Real PFCP, BGP MUP, and user packets remain
required; synthetic session injection is not a replacement.

## Local M1 candidate

Prerequisites are Ubuntu 24.04 x86-64, libvirt/KVM, Python YAML/Jinja2,
OpenSSH, curl, Git, qemu-img, virt-install and a working systemd user session.
`./lab deps` previews the package/group changes without requiring Python
modules. `sudo ./lab deps --apply` explicitly installs them and creates the
default image directory only if absent. Log out and back in after group
changes. Existing directory permissions are preserved; a custom image directory
must already exist and be writable. The installer does not install host Docker
or Go, or configure the reference lab. Fresh-host dependency installation was
validated on a new Ubuntu parent VM with nested KVM on 2026-09-20 JST, including
group membership after reconnecting. This is not second-physical-host validation.

New checksum-verified cloud image downloads use group `kvm` and mode `0640`,
including when the operator uses `umask 077`. This keeps the public backing
image readable after libvirt changes its owner. Existing cached images are
verified but their permissions are not changed automatically. If an older
cache is unreadable, its owner must grant the operator's `kvm` group read access
to that specific image; do not recursively change a shared image directory.

```bash
./lab deps
sudo ./lab deps --apply
./lab doctor
./lab up --build
./lab status
./lab health
./lab test one-call
./lab test baseline
./lab test lease
./lab test restart
./lab test network
./lab test neighbor
./lab test all
./lab evidence
./lab logs tpe
./lab shell tpe
./lab diagnose
./lab dashboard status
./lab destroy
./lab down
./lab up
```

`up --build` explicitly opts into local source builds **inside the new VM**.
It does not use or modify the reference guests, host Docker daemon, or host
kernel modules. The common runtime's application payloads use the isolated
builder with pinned toolchains and preserved source snapshots. All eleven free5GC NFs/WebUI are also built automatically from
locked sources, using the isolated M3 builder and a clean Ubuntu runtime base.
Subsequent component-level `rebuild` uses that same builder. No published
runtime or builder images are required.
`up` reconnects the UE and restarts the core/observer in dependency order;
it is not a hitless operation. Configuration file hashes trigger container
recreation when needed. Lifecycle/test operations have host and guest locks.
Kernel changes trigger a clean gtp5g rebuild, a strict vermagic check and a
coordinated module reload. They can require network access even with cached
application images. Authentication sequence numbers are preserved on updates.

Optional overrides live in ignored `config/compact.local.yml`, or use
`./lab --config /absolute/profile.yml ...`. This is a partial override of
`config/compact.example.yml`. Logical `networks`, `subscriber`, and `mup`
settings inherit the reviewed `lab.example.yml`, never the reference's
machine-local configuration. `dashboard_port` defaults to 8788 on the physical
host's loopback address and must be unused by other services.

The default VM has 4 vCPUs, 8 GiB RAM, an 80 GiB sparse disk and an independent
NAT management network. The launcher checks available memory, free storage,
route/network conflicts, and resource ownership. These allocations are
validation targets, **not yet demonstrated minimum system requirements**.
Resource collisions fail instead of adopting existing VMs. If an existing
network uses the sample subnet, select an unused subnet and matching gateway
and address in the local override before the first creation. VM settings are
immutable after creation; changing a profile does not migrate existing state.

`.lab/<vm-name>/` contains the dedicated SSH key, known_hosts, source transfer
archive and UUID ownership record. Keep this private and preserve the ownership
record while its VM exists. `down` stops the dashboard tunnel, waits for graceful
shutdown and retains all disks/sources. No VM autostart is set.

`./lab destroy` is a preview only. Applying requires
`./lab destroy --confirm srv6-mup-compact` (substitute the exact configured VM
name). It refuses ownership mismatches, extra disks, symlinks and other domains
using the network/disk. It gracefully stops and removes only the owned libvirt
definitions, renames the disk to a UUID-qualified `*-retired-*.qcow2` alongside
the original, and moves private state into `.lab/retired/<UUID>/`. No disks,
sources, pcaps or base images are deleted. Keep both archive locations private;
manual recovery requires the saved definitions/ownership and restoring the
original paths, not a new `up`. Archive handling is unit-tested and was exercised
on disposable validation VMs in the dated evidence below. The primary running
lab was not retired.

## Runtime boundary

Seven Compose bridge networks separate management, SBI/N4, N2, N3 access,
N3 core, SRv6 and N6. All are internal, with masquerading disabled. The N4
observer alone uses the **guest** network namespace to capture `br-free5gc`,
as UID 0 with all capabilities dropped except `CAP_NET_RAW`; this is not the
physical host network. Docker did not retain effective capture capabilities
with a non-root UID in the tested runtime. No Docker socket is mounted.
The UE shares the gNB's namespace but has a separate container lifecycle.
PEs have networking/BPF capabilities, not unrestricted privileged containers.
DN HTTP serves only its test response, never subscriber configuration.

The lifecycle waits for PE APIs and bootstrap, BGP sessions and the observer
lease before starting the gNB and UE. Failures are reported and the VM is
retained for diagnosis. Existing PFCP transaction acceptance and lease semantics
are unchanged. The ordinary UPF path is retained without N6 source NAT.
The functional generic-XDP profile disables TX checksum and segmentation
offloads on the DN/RAN data interfaces and PE interfaces. The initial DN
offload configuration passed ping but broke HTTP; the corrected profile is
checked with real TCP traffic, including checksums of N3 inner packets.
ICMP redirects are disabled so a baseline test cannot silently alter the
intended N6 next hop. Per-interface settings use Docker's
[endpoint sysctl mechanism](https://docs.docker.com/reference/cli/docker/network/connect/#set-sysctls-for-a-containers-interface---driver-opt).
NRF-generated certificates reside in private runtime state, not the pinned
upstream source tree; only NRF has a writable certificate mount.

## Dashboard and diagnostics

The display names are **MUP PE (N3/Interwork side)** and
**MUP PE (N6/Direct side)**. The Japanese dashboard uses
MUP PE（N3／Interwork側） and MUP PE（N6／Direct側）; `tpe`/`npe` appear only as
internal identifiers. Compose service names, JSON node/PE IDs, configuration
keys and commands such as `./lab logs tpe` and `./lab shell tpe` are unchanged.
Human-readable API PE names/roles now use the English labels; consumers should
match the stable IDs, not display names. See the [glossary](glossary.md).

`up` starts a guest-local systemd collector and a Compose web container, then a
supervised user-service SSH tunnel. Open `http://127.0.0.1:8788/` on the physical
host. The default is not externally exposed. Scope selection is explicit:

```bash
./lab dashboard start --tailnet
./lab dashboard start --local
./lab dashboard stop
./lab dashboard start
./lab dashboard status
./lab diagnose
```

`--tailnet` adds a binding only to the host's Tailscale IPv4 address; it does
not use a wildcard, Tailscale Serve or Funnel. The last successful scope choice
is retained for the next `up`; `--local` revokes that additional binding.
Tailscale installation/login and tailnet ACLs are the operator's responsibility.
The API is read-only but unauthenticated to anyone allowed to reach that port;
it exposes lab addresses/session information and should not be Internet-facing.

The collector is privileged **inside the dedicated VM** and uses a fixed set
of Docker status/exec operations, including one UE ping every five seconds.
It has no HTTP or command listener. Its atomic snapshot is mounted read-only
in the web container. The web process is UID 65534, has no capabilities, a
read-only root filesystem, `network_mode: none`, and no Docker socket, SSH key
or subscriber configuration. A separate writable directory contains its Unix
HTTP socket, reached through SSH forwarding; no guest TCP port is published.
The Unix socket allows local guest users to read the same API.

Snapshots older than 30 seconds, missing/invalid snapshots, or timestamps more
than five seconds in the future become `stale`/`offline`, clear active-path and
successful-probe indicators, and return HTTP 503 from `/healthz`. A fresh
snapshot returns 204 even if the lab itself is degraded: this endpoint measures
collection freshness, while `./lab health` checks live network readiness too.
Packet evidence tests pause only the periodic active probe via a shared lock;
read-only collection continues. Paused probes appear idle, not successful.
Five-second polling can miss brief registration/withdrawal transitions; pcaps
and logs are the authoritative evidence, not a complete dashboard event stream.

`diagnose` collects host/guest versions, ownership, service and network state
without raw logs, environment variables or authentication configuration; it
does not inject an active probe. Its skipped probe is reported as idle/degraded,
not a measured traffic failure. Output still contains local paths, UUIDs and
lab addresses and is not automatically a sanitized publication artifact.
The SSH tunnel restarts on failure while the user manager is running; it is
not a promise of host-boot autostart or survival after the last user logout.
The six-VM dashboard remains separate and unchanged.

## Component development

The experimental `source`, `rebuild`, `rollback` and `builds` commands support
`mup-controller`, `pfcp-observer`, `mupctl`, `mup-dashboard`, `vinbero`,
`ueransim`, and `free5gc-amf`, `free5gc-ausf`, `free5gc-chf`, `free5gc-nrf`,
`free5gc-nssf`, `free5gc-pcf`, `free5gc-smf`, `free5gc-udm`, `free5gc-udr`,
`free5gc-upf`, `free5gc-webui`. MongoDB is deliberately not rebuilt.

```bash
./lab source vinbero
./lab source free5gc-smf
./lab rebuild vinbero
./lab rebuild free5gc-smf
./lab builds
./lab rollback free5gc-smf
./lab health
```

`source` prints an editable **physical-host** directory. Original Go tools use
this repository; external components use ignored `worktrees/<component>/`
directories initialized at locked commits. Vinbero receives the reviewed MUP
patch once at initialization. Existing worktrees are never reset, cleaned or
overwritten; local commits and uncommitted edits are preserved. Ownership
records live in `.lab/source-records/`. An incomplete initial fetch or a changed
source lock currently requires operator diagnosis, not automatic deletion.
free5GC NF/WebUI commits are the gitlinks from the pinned
[free5GC v4.2.3 tree](https://github.com/free5gc/free5gc/tree/3b34a08e93a9b334f0f4005d3a3a9f79b66d59b9);
they are not inferred from mutable image tags.

`rebuild` copies a hashed source snapshot into the owned VM, then runs a
non-root builder with a read-only root filesystem and source mount, dropped
capabilities, no Docker socket or SSH/configuration mounts, a 3 GiB memory cap
and two CPUs. The builder can fetch build dependencies over the network. Go,
Node and Yarn downloads and the base image are checksum/digest-pinned. The
locally built builder is reused by immutable image ID and records installed
package versions. Apt repositories are not snapshot-pinned, so rebuilding the
builder on another day is not promised to produce the same bytes.

Git metadata, local configuration, keys, raw pcaps, build directories and
dependency caches are excluded from source transfer. Original Go snapshots
include `go.mod`, `go.sum`, `api`, `cmd` and `internal`, including new source
files; external snapshots use tracked and non-ignored new files. Symlink inputs
are rejected rather than followed. Keep custom source in those supported
locations. Source hashes cover input files and executable modes; build records
also retain effective module locks, builder/image IDs and artifact hashes.
Build snapshots, scratch space, caches and failed artifacts remain private
under `.lab/` on the host and guest and are not automatically garbage-collected.
History-free source distributions are supported: when the source directory
has no Git metadata, the optional commit field is null and file/mode hashes
identify the snapshot. Git is still required for external source worktrees.

Compilation failure does not change the selected/running images. After a
successful build, the selected services receive an immutable candidate image
layered on their existing runtime image. Dashboard changes restart only the
web container/collector and check snapshot freshness. Other components use the
conservative `up` reconnect sequence and real MUP packet/ICMP/HTTP gate: image
replacement is component-specific, but service restarts are **not hitless or
limited to that component**. The original Go test suite runs; Vinbero runs its
MUP tests and regenerates eBPF; free5GC tests are compile-checked with no test
bodies run, followed by the live lab gate. WebUI also builds its frontend with
an immutable Yarn lock. These are not full upstream conformance test suites.

Failed activation selects the previous images and verifies recovery. If that
also fails, it retains an unfinished transaction and reports failure; `up`
restores the prior selection before retrying. `rollback` restores the previous
successful image, not the source tree or database contents. Components sharing
a service (`mup-controller` and `mupctl`) must be rolled back in reverse change
order; a conflict is refused. Database/config schema migrations are outside this
image rollback contract. Do not prune retained image IDs needed for rollback.
Custom image selections persist across `up`, including `up --build`; a new
bootstrap candidate does not silently discard component overrides.
The complete common/NF baseline is selected atomically only after all builds
and staged Compose validation succeed. Failed builds retain the previous
candidate and active configuration; provisioning itself can still interrupt
services when a kernel-module rebuild is necessary. Bootstrap refuses an
unfinished development activation until ordinary `up` recovers it. A malformed
or incomplete new NF image set is an error, never an upstream-image fallback.
Legacy candidates remain readable, but gain the new baseline only after an
explicit build. Rollback to an absent custom override reveals the current
bootstrap baseline. MongoDB remains a pinned upstream pull with retained data.
In `builds`, `active` means that the build's exact image ID is selected for
all its services. An older controller image can therefore show false after a
later mupctl layer, even though that layer retains the controller binary.

These are local development candidates, not approved redistributable images.
Corresponding-source/license, SBOM, vulnerability and publication gates remain
M5 work. Common-runtime application payloads and initial NF builds use the
isolated builder; gtp5g kernel-module builds still use guest-native tools.
The legacy six-VM source trees are
never used or modified. NF source snapshots, effective module locks and build
records remain in private guest bootstrap directories, separate from editable
host worktrees. Each common runtime is built in a fresh output directory, so
all its Vinbero copies use the same dependency-floor check. Audits include
bootstrap NF images even when custom overrides are active.

## Evidence and test scope

`test one-call` requires fresh Registration/PDU logs, PFCP session deletion
and recreation, real T1/T2 advertisement, eight successful ICMP exchanges and
the expected HTTP response. It captures N2 SCTP, N4 PFCP and BGP UPDATE traffic.
The control capture must include PFCP establishment/modification/deletion
request-response types; it is not a complete NGAP or BGP conformance decoder.

Each traffic gate correlates echo identifiers/sequence numbers across N3/N6
and the SRv6 bridge, verifies GTP-U TEIDs against the observed PFCP session,
and requires zero matching user packets at both UPF N3 and N6 during MUP.
The baseline instead requires those packets on both UPF interfaces and none
on SRv6. BPF maps are resolved from each PE's attached XDP program and its
tail-call graph, not a global map-name lookup in the shared kernel. Capture
startup, truncation and packet-loss checks fail closed.

`test lease` stops the passive observer, proves lease expiry and ordinary UPF
fallback, then starts the observer and reconnects the real UE. A fresh passive
observer cannot reconstruct a preexisting session without new PFCP signaling.

`test restart` restarts only MUP PE (N3/Interwork side)/MUP PE (N6/Direct side) containers and runs the same managed
PE bootstrap as `up`. It verifies unchanged image/container IDs and changed
start times. `test network` force-recreates only those two containers, requiring
new container and network-namespace IDs. Both require restored SRv6 locator
routes, N6 membership in the correct VRF/table, and N6/UE fallback routes.
They prove MUP traffic and ordinary UPF fallback on the **existing PFCP session**
before a fresh 1call. These are managed recovery tests, not a promise of
unattended or hitless recovery after arbitrary daemon failures. Network bridges,
other containers, the physical host and the six-VM reference are not recreated.
This replaces the reference's PE netplan/networkd reconfiguration test with a
container network-namespace/endpoint recreation test, not an identical fault.

`test neighbor` removes only the dynamic DN neighbor entry on MUP PE (N6/Direct side). It pauses
the collector's active probe, observes the missing entry, and requires the
installed periodic MUP PE (N6/Direct side) refresh to restore it within 30 seconds, without a
manual ping or injected entry. Traffic and fresh 1call gates follow recovery.
Static neighbor entries are refused. Recovery results retain before/after
state and links to packet evidence; a failed gate remains failed even if
best-effort PE restoration succeeds. Packet metadata records the actual
running component image IDs, not only the original bootstrap candidate.

`test all` runs one-call + baseline/MUP + lease + restart + network + neighbor;
**clean builds, second-host reproduction and VM reboot acceptance are separate**.
These tests are disruptive and currently cover exactly one IPv4 UE.

Raw pcaps, per-PE BPF dumps and results remain under
`/opt/srv6-mup-compact/.lab/runtime/evidence/` in the guest. `./lab evidence`
lists completed result summaries, including failed recovery gates; failed captures are retained for
diagnosis. These new captures are not approved public examples, are excluded
from source export, and have not been uploaded. The compact dashboard's
periodic probe is excluded from the packet gates as described above.

## Clean reproduction procedure

Use a disposable Ubuntu 24.04 x86-64 host with KVM, adequate memory/storage,
and a history-free source copy owned by the operator. Do not copy existing
guest disks, `.lab`, developer worktrees or build caches into it. The example
below deliberately selects `config/compact.example.yml` instead of inheriting
machine-local overrides. Check subnet/port availability first; use a separate
profile if the example conflicts with an existing resource.

```bash
./lab deps
sudo ./lab deps --apply
# Reconnect so the new libvirt/kvm group membership is active.
./lab --config config/compact.example.yml doctor
./lab --config config/compact.example.yml up --build
./lab --config config/compact.example.yml test all
./lab --config config/compact.example.yml rebuild mup-dashboard
./lab --config config/compact.example.yml rollback mup-dashboard
./lab --config config/compact.example.yml down
./lab --config config/compact.example.yml up
./lab --config config/compact.example.yml test all
./lab --config config/compact.example.yml health
./lab --config config/compact.example.yml destroy
# Only on the disposable test environment, after inspecting the preview:
./lab --config config/compact.example.yml destroy --confirm srv6-mup-compact
./lab --config config/compact.example.yml up --build
./lab --config config/compact.example.yml test all
./lab --config config/compact.example.yml health
```

Retain private logs, source/lock hashes, both ownership records, guest runtime
and packet evidence, and the retirement archive. Verify the retired disk is
retained unchanged, the second VM has a new UUID, and the recreated VM uses a
new overlay of the pristine cloud image, not the retired disk. Reusing the
checksum-verified distribution base image is permitted; reusing the first
guest's sources, binaries or dependency caches is not a clean guest rebuild.
This sequence tests two clean **guest** builds on one prepared host, not two
independent host installations, byte-identical output or another physical CPU.

For fresh-host acceptance on the same physical machine, a new 12-GiB/6-vCPU
Ubuntu parent with nested KVM can host one 8-GiB/4-vCPU compact guest at a time.
Nested virtualization must be available; software emulation is not a substitute.
Account for the parent plus a host reserve before starting it. Stop only the
explicitly selected test lab if capacity requires it, then restore and check it
after stopping the nested guest and parent. Never suspend the parent with a
running nested guest. This test does not establish support for ARM/macOS.

## Private image audit

```bash
./lab audit-images
./lab audit-images --scan
```

The first command only previews the immutable image IDs selected by all 20
running services, plus the bootstrap candidate and the builder when present.
The second saves each distinct image through `docker image save` over the
owned VM's SSH connection. It never commits or exports a running container,
copies its writable layer/volumes, changes image selections, or publishes.
No host Docker daemon is required. Allow space for retained image archives
and the scanner cache; the launcher checks a conservative reserve first.
Before scanning, the archive's manifest/config/layer hashes are verified without
extracting files. Both classic Docker config IDs and OCI manifest IDs are
bound to the config ID and rootfs layer IDs reported by the scanner. A valid
OCI image is not rejected merely because its manifest and config digests differ.

Checksum-pinned Trivy runs on the host, serially with bounded parallelism,
against the saved archives. A fresh vulnerability database is downloaded once
and frozen for the run; tool/database metadata and the database SHA-256 are
recorded. Package vulnerabilities, licenses, filesystem secret patterns and
image-config secret patterns are scanned. Dependency lookup uses offline mode;
the database and scanner downloads still need network access. Ambient Trivy
environment/configuration and ignore files cannot silently suppress findings.
The implementation uses the [documented Trivy image flags](https://trivy.dev/docs/v0.74/guide/references/configuration/cli/trivy_image/).

Private output is under `.lab/<vm-name>/image-audits/<run>/`: image archives,
before/after inventory, raw JSON, per-image policy results, CycloneDX SBOMs,
scanner logs and a manifest. Directories are `0700`, output files are `0600`;
reports may contain secret matches and must not be uploaded as public examples.
The manifest distinguishes scan completion from policy success and always
sets `publication_approved` to false. HIGH/CRITICAL findings, unknown/unreviewed
licenses, secret matches, missing inventories, scanner failures, changed image
selection or an altered database cannot become a successful audit. A nonzero
exit keeps completed reports and failed-item diagnostics; it does not prune
images or silently retry with weaker checks. Rerunning creates a new private run.

This is an evidence-gathering step for M5, not a release gate that approves
distribution. The source-only host-tool license exceptions are not applied to
container images. Scanner-generated SBOMs are not proof of complete C/C++ or
embedded eBPF coverage. Separate work remains for all deleted/overwritten image
layers and build history, privacy beyond secret-pattern matches, corresponding
source archives and notices, guest OS/gtp5g, unselected upstream NF images,
signed provenance and anonymous pulls. A missing builder is explicitly recorded
as pending. The command inspects only the current compact VM; it neither scans
nor changes the six-VM reference. Repository source scanning excludes `.lab`
and `worktrees`; image scans are explicit and separate.

Image license classification now uses its own policy, not the provisional
source-only allowlist used in the initial checkpoint below. Recognized GPL/LGPL
packages generate corresponding-source and notice requirements, not automatic
approval. Dependency-floor updates, the explicit NF `--clean-runtime` rebuild,
key provenance and private material collection are documented in
[image distribution review](image-distribution.md).

### 2026-09-21 image review checkpoint

The corrected checker completed all 19 distinct selected images, including
the builder and MongoDB, and produced 19 nonempty CycloneDX SBOMs. It verified
18 OCI manifest identities and one multi-platform index's Linux/amd64 child,
with no scanner/config/layer identity mismatches. Container/image selection and
the frozen vulnerability DB stayed unchanged. Two earlier diagnostic runs are
retained privately; they exposed the distinction between Docker manifest/index
IDs and Trivy config IDs and are not the final acceptance record.

Trivy `0.74.0`, using the DB updated at `2026-09-20T19:19:55Z`, reported
391 HIGH and 21 CRITICAL advisory/package/version combinations after removing
duplicates across images and binary targets. These are scanner candidates,
not 412 proven exploitable paths in this lab. Ten images had one private-key
pattern match each: nine free5GC certificate-key files and one Go SDK file in
the builder. Their provenance, purpose and release treatment need review;
this does not establish exposure of operator credentials. Raw values remain
private. Unknown/unreviewed licenses also failed the conservative policy;
that is not a determination of license infringement.

The final record has `complete: true`, `policy_passed: false` and
`publication_approved: false`. No vulnerability or license finding was
suppressed. The original lab remained healthy with two established BGP peers
and a fresh dashboard. Repository checks passed 191 Python tests (19 for image
auditing), all Go tests, `go vet`, the applicable ShellCheck checks, bilingual
documentation checks and the 206-file source inventory.

The remaining M5 work is ordered as follows:

1. Review the flagged package versions and compatibility-preserving updates;
   rebuild minimal role-specific images rather than inheriting unused binaries
   and certificate fixtures from larger upstream images. Re-run forwarding and
   fallback gates after dependency/image changes; do not bulk-ignore findings.
2. Assemble exact corresponding sources, patches, build recipes and license
   notices for each intended distributed artifact. Supplement incomplete
   native/eBPF dependency coverage; source-only CI exceptions are insufficient.
3. Review every image layer and build-history field for private data, including
   deleted/overwritten content. Deleting a key in a later layer alone does not
   satisfy this review. Decide any fixture exceptions using exact provenance,
   not blanket private-key exclusions.
4. Repeat artifact-bound clean/reboot tests, then prepare signed provenance and
   publication candidates. New repositories/registry uploads still need the
   separate publication decision; anonymous-pull tests follow that decision.

Second-physical-host testing remains blocked by an offline test host, and
browser visual acceptance by the unavailable browser connection. Neither is
counted as passed by the image scanner.

## Milestones and acceptance

1. **M1 — validated locally:** dedicated VM, Compose topology, kernel compatibility,
   source-built local runtime, actual registration and bidirectional traffic.
   Require packet captures and PE-specific BPF evidence as well as ping/HTTP.
2. **M2 — implemented, acceptance partly pending:** resumable lifecycle, health,
   logs, component shells, explicit dependency installation, read-only diagnostics,
   split collector/web, automatic dashboard tunnel, opt-in Tailnet and confirmed
   ownership-scoped retirement with data retention. Fresh-host CLI installation
   and actual retirement/recreation passed in nested KVM; browser visual
   acceptance remains pending. These results are not based only on unit tests
   or a retirement dry-run.
3. **M3 — validated locally:** component `source`, `rebuild`,
   `rollback` and build records for original Go tools, Vinbero/eBPF, UERANSIM
   and deployed free5GC NFs/WebUI. Builder isolation, source preservation and
   transactional image selection are implemented. All 17 component builds and
   activation gates passed; real failure/rollback checks are described below.
4. **M4 — recovery and two clean guests validated locally, second host excluded:**
   baseline/MUP/lease/restart/network/neighbor-recovery gates, anti-bypass packet
   evidence and dashboard freshness checks passed. Two clean guest builds,
   including VM-reboot acceptance for each, passed on one nested-KVM parent.
   On 2026-09-24, validation on another supported physical machine was excluded
   from this acceptance scope by operator decision; it was not marked passed. Container
   network-namespace recreation covers the PE network recovery assertions;
   it does not test replacement of every Compose bridge or arbitrary failures.
5. **M5 — private image evidence implemented, distribution pending:** reviewed prebuilt OCI images and builders, exact SBOMs,
   notices and corresponding sources, image-layer/metadata privacy checks,
   signed provenance, anonymous pulls, bilingual documentation and a separately
   approved new public repository. MongoDB remains a direct pinned upstream pull.

No M2–M5 completion or public-distribution approval follows from passing M1.
On 2026-09-16, the 4-vCPU/8-GiB guest passed the local one-call, baseline/MUP
and lease gates on the 6.8 kernel series. The initial kernel was
`6.8.0-138-generic`; the guest's package updates selected `6.8.0-139-generic`
on reboot, exposing the stale-module build issue described above. Docker
Engine was `29.1.3` and pinned Compose `5.5.1`. Apt packages are not snapshot
pinned: these are observed versions, not a claim of byte-identical builds or
arbitrary kernel-update compatibility. Two clean builds, a second physical
host and the complete recovery matrix were unverified at that point. The research feature
backlog remains pending.

After the clean module rebuild, the running kernel and module vermagic both
matched `6.8.0-139-generic`. A further `down` → `up` reused the application
images without rebuilding, and one-call, baseline/MUP, lease and health checks
all passed again. The final MUP capture had 26 user packets each on N3, SRv6
and N6, and zero at either UPF interface. Repository checks passed 132 Python
tests (25 compact-specific), Go tests and `go vet`; no publication approval is
implied by those checks.

The M2 dashboard candidate was deployed on 2026-09-17 JST and remained healthy
when checked on 2026-09-19. Collector-stop testing produced stale/offline state
and HTTP 503, then recovered after restart. Both loopback-only and explicit
Tailnet-only additional bindings were checked; the final default remains
loopback-only. A TCP TIME_WAIT false-positive in tunnel restart checks was
fixed. Browser visual acceptance remains pending because no browser connection
was available. No images, new repository or new raw captures were published.

On 2026-09-19, a further `down` → `up` automatically restored the dashboard,
then one-call, baseline/MUP, lease and health gates all passed with collection
enabled. Killing the owned SSH tunnel process proved automatic reconnection.
Repository checks passed 142 Python tests (35 compact-specific), all Go tests,
`go vet`, bilingual documentation checks and the 199-file source inventory.
ShellCheck passed for the compact dependency installer and launcher. The
dependency installer was previewed, not applied again on the prepared host;
retirement was previewed on the live lab and applied only to unit-test fixtures.

On 2026-09-20 JST, all 17 supported components had successful source builds
and activation gates, including all 11 free5GC NFs/WebUI components. An initial
Go build exhausted the small tmpfs; moving build temporary files to guest-backed
scratch space fixed it without changing the running image on build failure.
An intentionally failing dashboard binary then proved activation failure and
automatic restoration of the previous working image. The fault injection was
removed. Explicit dashboard and SMF rollbacks succeeded; the custom images were
rebuilt/reselected afterwards. These two real rollback cases and transactional
unit tests are not a claim of live rollback testing for every component.

A subsequent `down` → `up` retained custom image selections and restored the
dashboard. The one-call, baseline/MUP, lease and health gates passed. The new
restart, network and neighbor gates also passed on the same custom-image lab:
both PE network namespaces were actually replaced, and DN neighbor recovery
succeeded while collector probes were paused. All packet captures/build records
remain private. No images, captures or new public repository were published.

Repository checks passed 167 Python tests (60 compact-specific), all Go tests,
`go vet`, bilingual documentation checks and the 204-file source inventory.
WebUI frontend builds completed with upstream peer-dependency/optional-patch
warnings; these were not silently fixed by changing its locked dependencies.
Passing local functional tests does not satisfy the outstanding M5 dependency,
license or distribution review.

Fresh-host acceptance on 2026-09-20 JST used a new 12-GiB/6-vCPU Ubuntu
parent with nested KVM, a history-free source copy and an 8-GiB/4-vCPU child.
Dependency installation and group activation succeeded without copying an
existing guest's runtime or build caches. External SMF source checkout and
history-free dashboard rebuild/rollback also succeeded. The exploratory first
child passed the complete packet/recovery suite, but its first VM restart
exposed a backing-image permission bug under `umask 077`. That attempt was
repaired only in the disposable test environment and is **not** counted as a
clean success of the corrected launcher. The new-download permission fix is
described above; cached images are not silently modified.

Actual retirement of that child removed only the owned VM/network definitions,
retained the unrelated libvirt default network, and preserved its disk byte
for byte (matching SHA-256 before/after). Ownership records, keys, XML and
guest-side sources/evidence were retained. The six-VM reference stayed running;
the existing compact lab was temporarily stopped for memory capacity.

Two subsequent clean children passed on 2026-09-20 JST with the corrected
launcher and no manual permission repair. Each used a new UUID, a new overlay
and a separately downloaded, checksum-verified cloud base under `umask 077`;
no guest sources, binaries or dependency caches were carried over. Both passed
`test all` before and after a VM `down` → `up`, including 1call, UPF fallback,
lease expiry and PE restart/network/neighbor recovery. Both ran
`6.8.0-138-generic`; the nine locally built executable SHA-256 values matched
between the two guests. This observation is not a guarantee of byte-identical
OCI images or future builds, because image metadata and apt dependencies are
not fully reproducible. The first corrected child's retirement also preserved
its disk SHA-256. Logs, ownership records, source/lock hashes and packet
evidence remain private. These are two guest reproductions on one newly
prepared parent, not two independent physical hosts.

On 2026-09-21 JST, the final child passed health again before both the child
and parent were gracefully stopped. The original compact lab was restored
with its custom image selections; the complete suite and health passed, and
the supervised loopback dashboard tunnel was active. The six reference VMs
remained running. Repository checks passed 172 Python tests (65 compact-specific),
all Go tests, `go vet`, ShellCheck for the compact launcher/installer, bilingual
documentation checks and the 204-file source inventory. The tested compact
launcher/runtime and dependency-lock hashes matched the local source.
Second-physical-host and browser visual acceptance, plus M5 distribution review,
remain outstanding. No new images, raw captures or public repository were
published, and these local changes were not pushed.

### 2026-09-23 automatic bootstrap acceptance

The updated initial-build path was exercised on a new 4-vCPU/8-GiB VM with an
80-GiB thin disk, on the existing physical host. Only the checksum-verified
cloud backing image was reused; no guest disk, application image, source tree
or build cache was copied from another lab. One initial build created the
common runtime and all eleven clean-runtime NFs, then passed real registration,
ICMP/HTTP and correlated N3/SRv6/N6/UPF packet checks without manual repairs.
The full suite subsequently passed before and after VM shutdown/restart.

Normal dashboard and SMF rebuilds, activation checks and explicit rollbacks
also passed. After rollback and reboot, all 20 running services matched the
baseline selections: eleven NF images, eight roles using the common image,
and unchanged MongoDB. The eleven NF provenance records and effective module
lock hashes matched, with no remaining development overrides. Reboot changed
the guest kernel from `6.8.0-138-generic` to `6.8.0-139-generic`; automatic
gtp5g rebuilding/loading succeeded and its vermagic matched the running kernel.
Application images were reused, not rebuilt. Docker was `29.1.3` and Compose
was `5.5.1`.

The exercised command sequence used a separate private profile:

```bash
./lab --config path/to/private-profile.yml up --build
./lab --config path/to/private-profile.yml test all
./lab --config path/to/private-profile.yml rebuild mup-dashboard
./lab --config path/to/private-profile.yml rollback mup-dashboard
./lab --config path/to/private-profile.yml rebuild free5gc-smf
./lab --config path/to/private-profile.yml rollback free5gc-smf
./lab --config path/to/private-profile.yml down
./lab --config path/to/private-profile.yml up
./lab --config path/to/private-profile.yml test all
./lab --config path/to/private-profile.yml audit-images --scan
```

Scoped remediation outcome: **fixed for initial image assembly/selection**.
The shared build-only helper, complete immutable NF baseline and separate
custom overrides close the upstream-image fallback while preserving rebuild
and rollback behavior. Regression tests cover malformed/partial image sets,
missing images, build/validation failure, pending activation, dirty bootstrap
sources, dashboard-only selection and audit coverage. Independent read-only
investigation and candidate review found no concrete remaining bypass or
regression. Local checks passed 219 Python tests, all Go tests, vet, ShellCheck,
bilingual documentation and the 218-file public-source inventory.

Implementation changes are in [bootstrap](../scripts/compact_runtime.py),
[component builds](../scripts/compact_develop.py),
[image selection](../scripts/compact_compose.py) and
[audit inventory](../scripts/compact_audit.py), with
[bootstrap regression tests](../tests/test_compact_bootstrap.py) and
[audit regression tests](../tests/test_compact_audit.py).

The separate [image distribution review](image-distribution.md) records the
artifact scan and unresolved distribution gates. This is same-host clean-VM
acceptance, not a second-physical-host test or a byte-identical OCI build claim.
Existing reference and compact labs were not migrated; their custom images
were preserved. No database migration, public repository creation, image upload,
commit or push was performed.

## Database and dependency acceptance on 2026-09-24

Fresh databases now select pinned MongoDB 8.0.32; existing volumes never change
major version implicitly. See [database migration and recovery](database-migration.md)
for the explicit upgrade commands, retained backup and interruption semantics.
The original compact lab and the isolated acceptance VM both passed the
4.4-to-8.0 migration and the complete packet/recovery suite. Existing custom
component selections were retained; CHF/WebUI were rebuilt from their source
trees with the reviewed dependencies. The six-VM reference was not migrated.

The acceptance VM also passed a restart on kernel `6.8.0-142-generic`, with
matching gtp5g vermagic. The builder uses `linux-libc-dev=6.8.0-142.142`.
WebUI selects SMF 1.4.5. The license-bearing afero-snd commit preserves the old
FTP API; a trial of 0.2.0 failed compilation, left the running selection intact,
and was not adopted. New dependency floors reject workspace-local bypasses.

These are operation and compatibility results, not image-redistribution
approval. The [image review](image-distribution.md) records the remaining
upstream tool and license/material gates. Browser visual acceptance is still
blocked by the absence of a connected browser; HTTP/API checks do not replace
it. Different-physical-machine testing is excluded, not passed.
