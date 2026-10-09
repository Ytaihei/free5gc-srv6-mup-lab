# Container image distribution review

English | [日本語](image-distribution.ja.md)

## Scope and decisions

Source publication, local lab operation, and redistribution of container images
are separate decisions. This workflow prepares **private review materials**;
it does not publish images, authorize a release, certify legal compliance, or
establish that every scanner advisory is exploitable. Keep generated files in
ignored `.lab/`, not in a GitHub release. Existing source-only release policy
in [third-party notices](../THIRD_PARTY_NOTICES.md) remains in effect.

The initial image audit used the source-only license allowlist provisionally.
That made ordinary GPL/LGPL OS packages fail as unreviewed, not as prohibited.
Image checks now use [image-distribution-policy.yml](../config/image-distribution-policy.yml).
The source CI continues using its separate, unchanged policy.

## Dependency remediation

[compact-dependencies.json](../config/compact-dependencies.json) specifies
minimum versions for external compact Go builds: x/crypto 0.55.0, x/net 0.58.0,
x/text 0.41.0 and gRPC 1.83.2. The original project's own module locks already
select the applicable newer versions. External sources are copied into build
scratch space before updating; editable host worktrees are preserved. Newer
stable custom versions are retained. Replacements or prerelease/pseudo versions
of these specific modules require review instead of being silently overwritten.
Effective module locks and the dependency policy/implementation hashes are
retained with each component build. Initial Vinbero bootstrap uses the same
minimum-version check. This does not modify the six-VM reference.

The builder still uses pinned Go/Node/Yarn. Unused npm/npx are removed in a
separate toolchain stage, so neither npm nor the original toolchain download
archives enter final builder layers. Yarn is invoked directly. The exact-hash
Go platform-verifier key fixture is removed before the final stage's COPY;
other public SDK test fixtures remain subject to scanning and provenance review.
This is not a secret-scanner exemption.

Replacing an NF binary on top of an upstream image does not replace its OS,
compiler, or older layers. The explicit clean-runtime option instead builds
the NF/WebUI on the pinned compact Ubuntu base. Only newly built payloads are
copied; configuration/certificates remain runtime mounts. UPF routing commands,
the WebUI frontend, existing database state, image activation checks and
rollback are preserved. It is not a database migration or a certificate rotation.

```bash
./lab rebuild vinbero
./lab rebuild free5gc-smf --clean-runtime
./lab health
./lab test all
./lab audit-images --scan
```

Use the clean-runtime option for each of the eleven supported free5GC
components when reviewing that profile; it is not supported for MongoDB or
original lab components. The normal rebuild command preserves its existing
parent-image behavior. Initial deployment now automatically builds all eleven
NFs on the clean runtime base, and builds a fresh common runtime with updated
Vinbero dependencies. The complete image set is selected only after successful
builds and Compose validation, without populating custom development overrides.
Existing overrides and legacy candidates are preserved; older deployed images
are not implicitly upgraded. Audit each actual profile separately.
Apt repositories are not snapshot-pinned;
keep exact installed versions and image digests for every actual artifact.

## Keys: provenance is not an exemption

The ten matches in the initial 19-image audit were compared against complete
file SHA-256 values, not just filenames or regex snippets:

- Nine files under the free5GC certificate directory match the publicly
  committed defaults in [the pinned Compose certificate tree](https://github.com/free5gc/free5gc-compose/tree/e4e2acebad6d6a8c49cfb03a97d9a09dd40c55c7/cert).
  The [upstream NRF image recipe](https://github.com/free5gc/free5gc-compose/blob/e4e2acebad6d6a8c49cfb03a97d9a09dd40c55c7/nf_nrf/Dockerfile)
  explicitly copies a default key into the image.
- The Go SDK match is used by [the platform-verifier test](https://github.com/golang/go/blob/go1.26.9/src/crypto/x509/platform_test.go),
  not an operator credential.

[public-test-keys.json](../config/public-test-keys.json) records paths, hashes
and upstream provenance, never key contents. Material collection compares the
saved image layers with these hashes; any changed, missing or differently
located key stays unverified. No secret-scanner exemption is added. Known
public keys must not become production authentication credentials. Fresh NF
layers omit embedded keys, but the isolated lab's runtime certificate mounts
are unchanged and are not part of an image audit. A key removed in a later
layer remains in older layers; deleting it is not sanitization.

## Image license criteria

This is an engineering checklist; actual obligations depend on the exact
texts, modification/linking and distribution/service arrangement. A recognized
identifier only selects review requirements. It does not approve a package or
the combined image. Preserve all scanner findings and obtain appropriate
license review before distribution.

| License family | Required review materials |
|---|---|
| MIT, BSD, Apache, ISC and other recognized notice licenses | Exact license/copyright/NOTICE texts and modification records |
| MPL | Those materials plus covered-file sources |
| LGPL | Corresponding source plus linking/relinking review |
| GPL | Corresponding source, modifications and build/install scripts |
| AGPL | GPL-family materials plus network source-access review |
| SSPL / MongoDB | Exact source/notices plus explicit distribution and service-scope review |
| Unknown metadata, ambiguous identifiers, compound expressions, public-domain claims | Exact package/version/file license identification; no automatic normalization or approval |

The implementation leaves `distribution_approved` and `publication_approved`
false. HIGH/CRITICAL advisories (including those without fixes), secret matches,
unknown licenses and artifact-identity failures still fail the scanner gate.
License classification, collection completeness and release approval are
separate fields. A recognized GPL label is no longer itself a scanner failure.
Read the [GPLv2 text](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt),
[AGPLv3 text](https://www.gnu.org/licenses/agpl-3.0.txt),
[MongoDB SSPL text](https://www.mongodb.com/legal/licensing/server-side-public-license)
and [Debian copyright-file policy](https://www.debian.org/doc/debian-policy/ch-docs.html#copyright-information)
when reviewing the corresponding packages; these links are not a source offer.

## Collect private materials

Run this after an image audit has completed. A failed scanner policy is allowed;
an incomplete scan or changed archive/report/SBOM hash is rejected.

```bash
python3 scripts/image-materials.py .lab/VM_NAME/image-audits/RUN
python3 scripts/image-materials.py .lab/VM_NAME/image-audits/RUN \
  --download-go-sources --go /absolute/path/to/locked/go
```

Replace the placeholders with the actual private audit and locked Go executable.
The second form explicitly downloads exact-version module archives using the
public Go proxy and checksum database; it never builds downloaded source.
Missing/invalid versions remain collection failures, not silently substituted
tags. Both forms create a new private directory under `.lab/distribution-materials/`
with permissions 0700 (files 0600), preserving old audit records.

The material set includes per-image package/version/source-package inventories,
license classifications and requirements, exact SBOMs, image/layer identities,
key-provenance classifications, and content-addressed license/notice texts from
all saved layers. Optional module ZIPs retain full module sources and checksum
identities; their license/NOTICE files are also collected. No archive paths or
links are extracted into the host filesystem, and key contents are not copied
into notice blobs. The collector does not reconstruct a final root filesystem:
superseded notice copies and unresolved links still need review.

`materials.json` records the evidence/policy/implementation hashes, collection
results and pending items. `complete` means the inventory walk completed, not
that every required source or license is present. Check module statuses and
`collection_failures` too; failed module collection exits nonzero. Unknown main
module versions require the exact build snapshots, not an invented release tag.

The initial material preparation inventoried 19 images, 1,750 layer-level notice
records and 489 distinct notice blobs. Of 200 versioned Go module requests,
198 yielded sources plus notices; one source ZIP lacked a detected license
file, and one could not be resolved by its reported version. These are evidence
gaps: afero-snd 0.1.0 needs exact-license review, and the reported gosu 1.19.0
does not resolve as a Go module tag. Do not substitute an apparently similar
version without binding it to the actual binary. This older inventory is not
proof about subsequently rebuilt images; collect again for the final audit.

## Release material acceptance checklist

Before attaching any image or binary, retain and review:

1. Exact image/platform/layer digests, fresh SBOMs and scanner database identity.
2. Every included component, OS package, static library, eBPF object, frontend
   asset and toolchain dependency; scanners do not cover all of these equally.
3. Applicable license/copyright/NOTICE texts, matched to artifact versions.
4. Exact corresponding sources, local patches, effective dependency locks and
   build/install scripts. For distro packages retain the exact source package
   and distro patches, not only the upstream project URL. Check any source-offer
   arrangement separately; these scripts do not issue one.
5. Modified main-program source snapshots and C/C++/eBPF inputs, including
   UERANSIM and kernel-module material where that artifact includes them.
6. LGPL linking, AGPL network-source and SSPL service/distribution reviews for
   the actual intended use. An SBOM or a generic license list is insufficient.
7. All-layer/history and source-archive privacy review. Generated material may
   still contain local paths, upstream test secrets or other unreviewed data.
8. A supported-base/dependency disposition, functional regression evidence,
   signed provenance/anonymous-pull checks if publishing images, and explicit
   human approval for the specific release.

MongoDB remains the pinned upstream image and persistent data is untouched.
Moving from its old major version requires a separately verified data migration;
do not swap a major-version tag or delete the database volume to turn a gate
green. Builder kernel-header advisories also remain visible: headers are not
the running kernel, but package matching alone is not an exploitability review.

## 2026-09-21 remediation evidence and remaining work

Outcome: dependency/build changes verified; **overall distribution readiness
remains blocked**. The 19-image rescan completed without image/config/layer
mismatches or changes to the selected containers. It used exactly the same
vulnerability database bytes as the initial scan, making the following counts
comparable. Counts deduplicate advisory/package/installed-version tuples across
images, not just CVE IDs; they are not counts of demonstrated exploit paths.

| Evidence | Before | After |
|---|---:|---:|
| HIGH dependency candidates | 391 | 231 |
| CRITICAL dependency candidates | 21 | 7 |
| Secret-pattern matches | 10 | 1 |

The updated Vinbero PE image and ten NF images excluding WebUI have no
HIGH/CRITICAL matches. All eleven fresh NF images have no secret-pattern
matches. The remaining key match is the hash-verified public Go test fixture,
not exempted. Builder npm dependency findings disappeared after removing the
unused distribution, without disabling its scanner. No private key was rotated
and no running volume was scanned or copied into an image.

Remaining dependency candidates are explicitly tracked:

- MongoDB's unchanged upstream image: 60 HIGH / 2 CRITICAL and an old OS base.
  A compatible, data-preserving upgrade needs a separate migration plan/test.
- Builder kernel headers: 166 HIGH / 5 CRITICAL in linux-libc-dev; the scanner
  reports no fixed package version. A contextual review or a compatible newer
  build baseline is still required; these are not findings about the host kernel.
- WebUI: one HIGH match for its embedded free5GC SMF module (CVE-2026-44321),
  with no fixed version reported. Compilation and traffic success do not waive it.
- Common bootstrap/observer/dashboard/controller/RAN images still carry older
  Vinbero copies, even though their selected PE daemon is updated: four unique
  HIGH dependency candidates. Replace/rebuild these common-image copies or split
  the runtime payloads, preserving any component customizations. Updating the PE
  alone does not sanitize every image or old retained layer.

Post-update material collection covers all 19 images, 2,663 layer-level notice
records and 349 distinct notice blobs. There are 181 Go module requests:
179 collected sources plus notices, one ZIP without a detected license file,
and the same unresolved gosu module version. These are private, unapproved
materials, not a complete redistribution bundle. File-level license detections
without package metadata are also retained as separate review requirements.

Validation passed the full compact suite (fresh 1call, ordinary UPF/MUP,
observer lease, PE restart, network recreation and neighbor recovery), plus each
component's activation/packet gates. An AMF rebuild after the provenance-field
correction also passed activation and real traffic. The final health check
confirmed two BGP peers, observer lease, T1/T2, UE ping and a fresh dashboard.
Local checks passed 208 Python tests, Go tests/vet, ShellCheck, bilingual docs
and the public-source inventory. Source/built-original-tool supply-chain scans
returned zero policy findings; the allowlisted source snapshot passed redacted
Gitleaks with its existing exact test-key exception, unchanged.

The fix-finding review traced both bootstrap and component-build paths and
added regression tests for dependency floors, replacement/version handling,
key-hash mismatches, license obligations and archive boundaries. Independent
agent review was unavailable; a separate local review pass found and corrected
missing file-level license requirements. No reachability-based CVE waiver is
claimed. No commits, pushes, new public repository, binary release or database
migration were performed. Old images, source snapshots and audit records remain
private for recovery and comparison.

## 2026-09-23 fresh-bootstrap image review

The new automatic bootstrap was verified on a separate clean VM, including
the complete packet/recovery suite before and after reboot, dashboard/SMF
rebuilds and rollbacks. See [compact acceptance](compact-lab.md). Initial NF
assembly now reuses the component builder and selects a complete immutable
baseline only after successful staging validation. Independent fix-finding
investigation/review and regression tests checked default selection, failure
states, custom overrides and audit coverage. The original upstream-NF selection
problem is fixed for this path; existing labs and their custom images were not
silently migrated.

All 14 image archives and 14 nonempty SBOMs were verified. The audit finished
with `complete: true`, `selection_unchanged: true`, `database_unchanged: true`,
`policy_passed: false` and `publication_approved: false`. Trivy `0.74.0` used
the database updated at `2026-09-23T01:09:35.013781075Z`. This differs from the
September 21 database, so the overall counts are not a controlled before/after
comparison. Counts below deduplicate advisory/package/installed-version tuples.

| Image group | HIGH | CRITICAL | Secret-pattern matches |
|---|---:|---:|---:|
| New common runtime, used by eight roles | 0 | 0 | 0 |
| Ten NFs excluding WebUI | 0 | 0 | 0 |
| WebUI | 1 | 0 | 0 |
| Unchanged MongoDB | 60 | 2 | 0 |
| Builder | 166 | 5 | 1 |
| Total after deduplication | 227 | 7 | 1 |

Both Vinbero binaries in the common image contain x/net 0.58.0, x/text 0.41.0
and gRPC 1.83.2. The old common-image copies therefore do not recur in this
fresh deployment; updating only the PE override is no longer necessary for
initial builds. This is not a claim that old retained images were sanitized or
that the new images have no lower-severity or unknown findings.

The builder's one key match again has the full SHA-256 recorded for the public
Go SDK test fixture; all layer copies at that path matched. It is still not
exempted. No raw match values are published. Remaining dependency actions are
MongoDB's separately tested data-preserving upgrade, builder-header disposition,
and WebUI's embedded SMF advisory. The earlier corresponding-source/notice
collection is bound to different images: a complete, reviewed release bundle
for these 14 artifacts remains pending, along with layer/history privacy review
and publication approval. License requirements are not suppressed by functional
or vulnerability-test success.

Local checks passed 219 Python tests, Go tests/vet, ShellCheck, bilingual docs
and the 218-file source inventory. The test VM was gracefully stopped with its
disk and private evidence retained; the original lab's final health check passed.
No commit, push, registry upload or database migration was performed.

## Updated artifact review on 2026-09-24

The updated 14-image baseline was built and tested after the explicit MongoDB
migration and a restart on the 6.8.0-142 kernel. Both compact labs passed the
complete communication/recovery suite. An initially empty MongoDB 8.0 volume
also passed WebUI initialization and one-call tests; the previously migrated
volume was then restored and both volumes retained. See
[database operation and recovery](database-migration.md).

Trivy 0.74.0 used the database updated at
`2026-09-23T12:53:55.964849051Z`. All 14 saved images and 14 SBOMs were complete;
image selection and scanner DB remained unchanged during the audit.
The raw policy still fails. Counts below deduplicate advisory/package/version
tuples; these are not direct before/after counts using an identical scanner DB.

| Image group | HIGH | CRITICAL | Secret-pattern matches |
|---|---:|---:|---:|
| Common runtime and all 11 NFs | 0 | 0 | 0 |
| Official MongoDB 8.0.32 image | 33 | 1 | 0 |
| Builder with updated headers | 166 | 5 | 0 |
| Deduplicated total | 199 | 6 | 0 |

MongoDB's remaining matches are in shipped Go tools and js-yaml, including
Go 1.24.6/1.26.5 and x/crypto 0.54.0. Updating the database server does not
automatically update those upstream-image components. No blanket reachability
exception or approval to redistribute that image has been added.
The builder findings refer to linux-libc-dev 6.8.0-142.142.
[Ubuntu's kernel team explains](https://lists.ubuntu.com/archives/kernel-team/2024-February/148886.html)
the distinction between non-executable userspace headers and the booted kernel.
Keep this contextual assessment separate from raw scanner results; it is not
a vulnerability assessment of the host/guest kernel or a reason to remove
required C/eBPF headers.

WebUI's SMF floor is now 1.4.5. Source tracing found that WebUI imports the SMF
configuration package, not the vulnerable UPI implementation; the module update
is conservative dependency hygiene, not proof of a formerly exploitable WebUI
endpoint. Its new binary has no HIGH/CRITICAL match in this audit. The root
module exemption now applies only to the actual component directory, and a real
Go workspace reproducer verifies rejection of a secondary local SMF module.
Independent fix-finding investigation and review also found a missing-DB-state
fallback; tests now reject both stale-backup and empty-volume replacement paths.

The builder verifies and excludes the public Go platform-root fixture before
copying the toolchain into final layers. Builds remain functional, including
external NF/WebUI builds. No credential is treated as safe merely because its
filename resembles a fixture. The additional all-layer check reads every
regular member and image config without extraction or symlink following:

```sh
python3 scripts/image-layer-review.py /private/audit \
  --denylist /private/publication-identities.json \
  --upstream-go-archive /private/locked-go.tar.gz
```

For this final artifact set, known identity text and packed-address checks
found zero candidates. Two remaining Go example-key files match the locked
upstream archive. The six embedded GnuTLS key blocks match the public
[GnuTLS 3.8.3 self-test source](https://github.com/gnutls/gnutls/blob/3.8.3/lib/crypto-selftests-pk.c).
Other matches in libssh/mongosh are delimiters without a complete PEM block in
this bounded check. The raw pattern records remain preserved: this finite
pattern/identity review does not prove absence of all encoded secrets.

All 185 versioned Go dependencies now have collected source archives and notice
texts, with zero collection failures. CHF/WebUI select the license-bearing
[afero-snd commit](https://github.com/fclairamb/afero-snd/commit/7844d753b751c069164261a0b0105561a348d80f),
fixed as `v0.1.1-0.20220917220349-7844d753b751`. Its Go source is unchanged from
0.1.0, preserving the old logging API. The 0.2.0 trial was incompatible with
the existing FTP dependency and was not activated. Unknown local replacements
and pseudo versions require review rather than silently taking this exception.
The [gosu 1.19 release](https://github.com/tianon/gosu/releases/tag/1.19) maps to
an exact commit/pseudo version because its release tag is not Go-semver; the
scanner version is retained separately. Its saved-image binary also matches
the official release binary hash. No similar-looking tag was substituted.

These are private review materials, not a complete redistributable bundle.
Exact distro/native/frontend sources and build correspondence, remaining
license-label review, upstream tool findings, signing, anonymous pulls and
explicit image-publication approval remain open. A source-only candidate is
separate from an image release. The old repository remains private; browser
visual acceptance and the name/approval of the new public destination require
operator input. Different-physical-machine testing is excluded, not passed.

## Collecting exact Ubuntu source packages

The distro-source collector complements the Go/notice material collector above.
It reads an existing immutable-image audit, without starting or modifying a VM:

```sh
python3 scripts/image_sources.py .lab/VM_NAME/image-audits/RUN --scope all
python3 scripts/image_sources.py .lab/VM_NAME/image-audits/RUN \
  --scope all --download --max-gib 8
```

Without `--download`, it only verifies the saved evidence and reports inventory
counts. The default scope is `runtime`; `builder`, `upstream` (the directly
pulled database), and `all` are separate choices. `--jobs` bounds concurrent
metadata lookups to 1–4 (default 3); archive writes remain serialized to protect
the shared budget and cache. Collection is **private review
preparation, never image or source publication**. It does not install, extract
or execute any downloaded source, and does not alter runtime selections or CVE
policy. Unsupported operating systems and missing exact source identities
remain explicit gaps. Currently only Ubuntu 24.04 is supported.

Before any network requests it checks audit completeness, unchanged image/DB
selection, archive/report/SBOM hashes, image configuration and layer digests,
and the scanner's binding to that same image. It retains each binary-to-source
mapping. Trivy's separate epoch, version and distro-revision fields are joined
without dropping Ubuntu patches; source epochs never inherit a binary epoch.

Exact versions are resolved through the official Noble/primary archive in the
[Launchpad API](https://api.launchpad.net/devel/). No nearest-version, latest
version, PPA or binary-version fallback is used. HTTPS and every redirect are
restricted to Launchpad's API/download infrastructure. Each
[Debian source descriptor](https://www.debian.org/doc/debian-policy/ch-controlfields.html#debian-source-control-files-dsc)
must name the exact source/version, and its SHA-256 checksums and sizes must
match every downloaded archive, including distro patches. This is HTTPS and
content-integrity checking, **not OpenPGP signature verification or proof that
the binary was built from those sources**.

Outputs under ignored `.lab/distro-sources/` have private directory/file
permissions. `sources.json` binds the audit manifest and collector implementation
hashes to exact packages, publication records, descriptors and content-addressed
source blobs. Archives are deduplicated within a run. Metadata and individual
files have size limits, the archive budget is reserved before each download
(including failed attempts), and 2 GiB of disk space is kept in reserve. Partial
files and completed evidence are retained on interruption; rerunning creates
a separate collection, not an in-place resume. Interrupted collections remain
incomplete; any collection failure or unresolved identity produces a nonzero
exit status after recording available evidence.

`complete` means the collection loop finished. `collection_complete` means only
that all **selected OS source packages in the requested inventory mode** were collected with
matching checksums. `publication_approved` and each source's
`distribution_approved` remain false. No license or scanner exception is created.
Review notices/licenses, source privacy, signatures and build correspondence
separately. Non-distro C/C++/eBPF, frontend and toolchain inputs, and old package
versions retained in overwritten lower layers are **not covered by the default final
OS inventory**; use the all-layer metadata mode below for recorded old versions.
Rebuilds require fresh artifact-bound evidence; successful
collection from an older image never approves a newer one.

## 2026-09-24 source-material and clean-candidate results

The collector was run against the fourteen saved images from the earlier
post-upgrade audit. Of 142 distinct source-name/version pairs, 139 were collected
with matching descriptors and archive hashes. The 290 distinct archive blobs
total 968,470,956 bytes; their saved hashes were checked again after collection.
The counts below overlap between groups and must not be added together.

| Selected group | Images | Final-OS sources collected / required |
| --- | ---: | ---: |
| Common runtime and eleven NFs | 12 | 104 / 104 |
| Builder | 1 | 117 / 117 |
| Directly pulled upstream database | 1 | 83 / 86 |

The three unavailable exact Ubuntu-primary sources are
`mongodb-database-tools` 100.18.0, `mongodb-mongosh` 2.11.1 and `mongodb-org`
8.0.32. They need separate upstream source/build/licensing work if included
in a distribution; they were not replaced with different versions or waived.
Consequently the all-scope run has `complete: true`, `collection_complete: false`
and `publication_approved: false`, and exits nonzero. This does not change the
policy of pulling the database directly from its upstream rather than mirroring
it. Interrupted preliminary collections were retained as incomplete evidence.
The final collector explicitly preserves distro revisions and source epochs.

Separately, a newly created same-host VM with no lab-image/build cache passed
source startup, MUP-C rebuild, one-call, rollback and the entire packet/recovery
suite. The candidate script was invoked locally, not by a registered GitHub
runner. All fourteen new image archives and SBOMs were then audited with Trivy
0.74.0 and a DB updated at `2026-09-24T09:10:28.608412934Z`. The audit was complete
with unchanged selection and scanner DB, but `policy_passed: false` and
`publication_approved: false`. Deduplicating advisory/package/installed-version
tuples again gave runtime/NF HIGH 0 and CRITICAL 0, upstream database HIGH 33 and
CRITICAL 1, and builder HIGH 166 and CRITICAL 5. All fourteen images had zero
scanner secret matches. Lower-severity/unknown findings and unresolved license
classifications remain; these figures are not an overall safety approval or a
same-DB before/after comparison.

The candidate command correctly failed its distribution gate after passing the
functional stages. Its final cleanup stopped the VM and retained all disks,
sources and private evidence; the original lab remained healthy. The source
materials above belong to the **earlier** immutable image set, not automatic
approval or complete corresponding-source coverage for this new build. Native,
frontend, toolchain and replaced lower-layer sources, license/build review,
protected-runner provisioning, signing, publication approval and anonymous
clean-pull acceptance remain pending. No images or materials were published.

## All-layer metadata and reusing source materials

```sh
python3 scripts/image_sources.py .lab/VM_NAME/image-audits/RUN \
  --scope all --all-layers
python3 scripts/image_sources.py .lab/VM_NAME/image-audits/RUN \
  --scope all --all-layers --download --max-gib 8 \
  --reuse .lab/distro-sources/PREVIOUS_COMPLETED_RUN
```

`--all-layers` reads recorded dpkg status, old status and split status files in
every verified saved layer, without extracting them. Deleted or overwritten
layers remain in the union. It preserves exact source versions, including
exact embedded-source relationships. Source-field omission follows
[Debian's defined source-field semantics](https://www.debian.org/doc/debian-policy/ch-controlfields.html#source),
not a guessed upstream version. Each mapping retains its image ID, layer digest
and package-metadata path/hash. A final scanner identity missing from that union
is a coverage failure. Links are not followed; unsupported metadata remains a
gap. This covers **recorded package metadata**, not arbitrary unrecorded payloads
or all source/license obligations.

Repeatable `--reuse` selects a previous private collection whose loop completed;
successful exact-version records can be reused even if other packages failed.
Interrupted loops are rejected. Metadata, exact publication identity,
descriptors and blob hashes/sizes are rechecked before copying into the new
collection. Copies are not hard links; copied bytes share the `--max-gib` budget
with downloads. Conflicting records, tampering and symlinks fail closed, without
a silent network fallback. The previous manifest hash is retained. This is an
explicitly selected, unsigned local cache, **not independent origin/signature
verification or distribution approval**. No credentials or archives are
uploaded, and old collections are preserved.

On 2026-09-25 (JST), the fresh candidate's fourteen saved images were inventoried
again. Both final and all-layer metadata modes identified 141 distinct
source/version pairs; this particular build had no additional old version
recorded only in lower layers. All 138 available Ubuntu pairs were collected
using reverified local copies (968,439,864 bytes); no source archive needed a new
download. The three MongoDB-related exact versions listed above still fail
Ubuntu-primary lookup. Runtime/NF coverage is 104/104, builder 119/119 and
upstream database 83/86 (overlapping counts). All-layer metadata had no unresolved
scanner-to-package identities, but overall collection and publication approval
remain false. This evidence is bound to the newer saved image set; it does not
reuse the older audit's approval or scanner results.

## Collecting recorded NF build sources

```sh
python3 scripts/image_build_sources.py .lab/VM_NAME/image-audits/RUN \
  --config /private/owned-vm-profile.json
```

The explicitly selected owned VM must already be running. This command does
not start it, rebuild or activate images, change the main lab, or execute any
collected source. It verifies the saved audit first, then reads only the
candidate and its eleven NF build records, source snapshots, manifests,
build scripts, runtime recipes and effective Go locks. It does not copy guest
home directories, credentials, container writable layers or database volumes.
The candidate must remain unchanged throughout the collection.

Source member hashes/modes and manifest identities must match the build record;
effective module locks and build scripts/recipes are also rehashed. Artifact
hashes are checked against the **final payload in the saved image**, respecting
later layer replacements and whiteouts, without following links. Source
archives are inspected, not unpacked or run. Bounds and private permissions
apply; errors retain incomplete evidence and return a nonzero exit code.

Outputs stay under ignored `.lab/build-sources/`. Its `build-sources.json`
records audit/implementation hashes, per-file evidence hashes, verified payload
counts and gaps. At the 2026-09-25 check, `collection_complete` covered only the
selected eleven NF build records, not all corresponding sources. The extended
collector described below additionally requires common-runtime and frontend
records. `publication_approved` stays false.
Recorded relationships and matching payload bytes are **not signed provenance
or proof of a bit-reproducible build**.

The 2026-09-25 (JST) check collected all eleven records: 1,040 source-snapshot
files and 17 matching image payload files. WebUI's declared package manifest,
Yarn lock and configuration were retained and hashed. These were build-input
records, not a complete frontend dependency-source/license bundle or verified
effective dependency archives. That common-runtime candidate had no equivalent
build-time snapshots for its own programs, UERANSIM C++ and Vinbero/eBPF. Current
mutable checkouts must not be labelled as those missing historical inputs;
recording inputs in a future isolated rebuild remains necessary. Toolchain
coverage, source privacy, license/linking review and the distribution gates
above remain pending. All materials stay private.

## Build-input capture for new candidates

New source builds compile the four own Go programs, Vinbero/eBPF and UERANSIM
inside the same restricted builder used for component development. Each build
uses a preserved source snapshot, not the mutable checkout directly. The
candidate records six common-runtime payload records, builder identities,
effective Go locks, build scripts and the runtime recipe. Own license/notices
are retained next to the snapshots, outside the image. The collector also
compares all nine common-runtime binaries against the saved image. Older
candidates without these records remain explicitly incomplete; no historical
provenance is invented from a current checkout.

WebUI now uses a fresh per-build Yarn cache, keeps immutable installation, and
preserves the effective frontend locks plus the exact dependency ZIPs. These
inputs are packed privately outside the runtime image, with per-file and archive
hashes. The collector verifies the complete recorded set and counts embedded
notice paths without executing or unpacking package sources. Missing notices
remain visible; matching hashes do not approve licenses or prove upstream
authenticity. Scoped package names are supported without permitting traversal.
The build fails before candidate adoption if provenance is incomplete.

The extended collector reports NF, common-runtime and frontend completeness
separately; its overall collection flag requires all three. It still does not
certify source correspondence, linking, licensing, toolchain coverage, signatures
or publication. A successful functional build and a failed distribution gate
are compatible results. Runtime, optional builder, upstream database and the
history-free source candidate must receive separate decisions.

## 2026-09-28 decision evidence (JST)

The isolated candidate VM was rebuilt with the new build-input capture. This
was a rebuild of the previously tested fresh VM, **not another empty-cache or
second-physical-host test**. Registration/PDU establishment, baseline and MUP
packet paths, observer lease expiry, restart, network/neighbor recovery, then a
controller component rebuild, 1call and rollback passed. The main lab was not
redeployed. The candidate VM was stopped afterwards; its disks and private
evidence were retained.

The fourteen immutable saved images were scanned with Trivy 0.74.0 and a
database updated at 2026-09-27 07:04:17 UTC. Image selection and database bytes
were unchanged across that audit. Counts below deduplicate advisory ID,
package name and installed version within each group; they are scanner
candidates, not demonstrated exploits or a before/after comparison using the
same database as older audits.

| Group | Images | HIGH | CRITICAL | Distribution decision |
| --- | ---: | ---: | ---: | --- |
| Common runtime and eleven NFs | 12 | 0 | 0 | Pending source/license and release gates |
| Optional builder | 1 | 162 | 5 | NO-GO |
| Upstream database | 1 | 33 | 1 | NO-GO for redistribution; do not mirror |

Trivy reported zero secret matches. A separate all-layer check covered thirty
distinct layers and found zero configured private-identity matches. Two Go
test files matched the locked upstream archive. Six other PEM-pattern matches
remain **unverified** in library/database binaries, including two complete-PEM
pattern matches. Do not equate scanner silence or recognizable filenames with
verified key provenance, and do not add blanket exemptions.

Private collection completed for all eleven NF records, six common-runtime
records and nine matching common binaries. The WebUI inputs contain 492 exact
dependency ZIPs and 485 detected notice paths; seventeen archives have no
detected notice. The collector now accepts the pinned icon package's more than
31,000 members, while retaining a 100,000-member limit and the existing
compressed/expanded byte bounds. These archives are not executed or extracted.

Go material collection retained 185 module records without collection errors.
All-layer OS metadata required 141 exact source/version pairs: 138 were
collected; the three MongoDB-related versions listed above remain unavailable
through this collector. Effective frontend inputs can themselves contain
prebuilt native binaries. Therefore these successful collections still do not
prove complete corresponding source, toolchain/linking compliance or a complete
license/notice bundle. Unknown license metadata remains unresolved.

Prebuilt release acceptance also has a known conditional dashboard issue:
explicit release startup creates the guest runtime parent root-owned mode
0700, preventing the ordinary SSH user from traversing to the child socket.
Child/socket permissions do not repair that parent boundary. Fix the socket
placement/access design without exposing private runtime state, and test a
fresh explicit-release startup before enabling distribution. The default
manifest remains `release: null`. Anonymous digest pulls, publisher identity,
signatures and final approval are not established. **No image is approved for
distribution by this evidence.**
