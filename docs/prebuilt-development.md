# Prebuilt startup and editable development

English | [日本語](prebuilt-development.ja.md)

## One lab, two ways to obtain images

Use the same single VM, Compose topology, configuration, dashboard and tests
for both ordinary use and development. Development does not create another
network. See the [compact setup guide](compact-lab.md) for host requirements.

For a guided exercise using the currently available source-build path, including
a visible dashboard change and rollback, use the [hands-on guide](hands-on.md).

| Purpose | Image acquisition | Builder |
| --- | --- | --- |
| Start a released lab | Pull the pinned common runtime and eleven NF images | Not downloaded |
| Customize one component | Snapshot edited source, rebuild and activate that component | Matching image downloaded on demand |
| Build without distributed images | Explicit local source build | Built locally from the committed recipe |

**No release images are published yet.** The bundled
`config/compact-release.json` deliberately contains `release: null`. First
startup with plain `./lab up` stops before creating a VM. For now use
`./lab up --build`; subsequent plain startup reuses the existing local baseline.
The implementation of image acquisition is not redistribution approval.

## User workflow

Once a reviewed release manifest and images are provided together, use the
matching source archive/tag. These commands are the intended release workflow;
until then, replace the first startup with the explicit local build above.

```bash
./lab deps
sudo ./lab deps --apply
./lab doctor
./lab release
./lab up
./lab health
./lab test one-call
./lab source mup-controller
./lab rebuild mup-controller
./lab rollback mup-controller
```

Host prerequisite installation may require logging in again. Edit the path
printed by `source` before `rebuild`. Own Go components use the main source
tree; external components use preserved editable worktrees. Uncommitted edits
and local commits are included. All unrelated component overrides remain in
place. Rollback restores an image, not source files or database contents.
Startup and most component activations reconnect the UE; they are not hitless.

If a release has no separately reviewed builder, rebuilding fails with an
explicit instruction rather than silently compiling a different environment.
Operators can choose the committed local recipe:

```bash
./lab rebuild mup-controller --local-builder
./lab rebuild free5gc-smf --local-builder --clean-runtime
```

Local candidates keep their existing automatic local-builder behavior.
Local reconstruction pins the base, tool archives and recipe but does not
claim bit-for-bit reproducibility: distribution package repositories are not
snapshot-pinned. Source changes to runtime orchestration or dependency locks
require a matching new release or an explicit local build. Go application
edits are supported after installation without invalidating the builder.

## Release contract and safe failure

`scripts/compact_release.py` validates a versioned manifest. The outer keys
are `schema_version` (integer 1) and `release`. A non-null release has exactly:

| Field | Meaning |
| --- | --- |
| `version`, `platform` | Version such as v0.1.0; Linux amd64 only |
| `compatibility_sha256` | Hash of guest orchestration, templates, recipes and source/toolchain locks |
| `source_sha256` | Hash of the own Go source snapshot, including file modes |
| `runtime` | Common image: `reference` at a GHCR SHA-256 digest and `image_id` |
| `nf_images` | The same image descriptor for each of all eleven NF service IDs; never DB |
| `builder` | Null, or `image` descriptor plus exact `inputs` (recipe, base, headers, toolchains) |
| `database` | The existing locked upstream MongoDB reference, not our mirror |
| `dashboard_snapshot_protocol` | Integer 1 |

The committed manifest is trusted input, **not a signature**. A maintainer
must bind its image IDs/digests to reviewed build, scan and licensing evidence.
Printing current source fingerprints neither builds nor approves images:

```bash
python3 scripts/compact_release.py --inputs
./lab release --manifest /absolute/trusted-release.json
./lab up --release /absolute/trusted-release.json
```

Explicit manifest selection installs into a fresh profile or retries the same
release. It refuses replacing a different existing baseline. Use a separately
named VM/profile for release comparisons; live baseline upgrades are not
implemented. Never remove ownership records or database state to bypass this.

Image pulls use the exact registry digest and Linux amd64 platform; inspection
also checks the returned image ID and repository digest. All twelve images
and a staged Compose configuration must validate before baseline adoption.
An interrupted pull retains downloaded cache but no partial baseline. No
mutable-tag or source-build fallback is used. An already installed image set
can restart without pulling its builder. Pending component/database recovery
blocks release installation. MongoDB is fetched directly from upstream by
the existing startup path, and its database is never implicitly migrated.

Guest provisioning still installs Docker and builds the pinned gtp5g module
against the running kernel. Prebuilt startup is not an offline appliance and
does not eliminate kernel build prerequisites, including GCC/build-essential.
Additional application tools (Go, CMake and Clang) are installed only for an
explicit full local build; component builds
use the isolated non-root builder without Docker sockets or subscriber keys.

## CI and remaining distribution gates

Ordinary CI runs offline manifest, acquisition, failure and builder tests as
part of `make check`. The optional Compact image candidate workflow builds a
new VM, checks component rebuild/rollback, runs the full packet/recovery suite
and performs the private image audit. It has no publication or artifact-upload step. It runs only manually
from the default branch, after setting `COMPACT_CANDIDATE_CI` to `enabled`, on
a dedicated one-job Ubuntu/KVM runner labelled `srv6-mup-release`. Configure
the `compact-candidate` environment with review protections first. It needs
the documented host dependencies, Go/Ansible test tools, sufficient RAM/disk,
and a working user service session. No untrusted PR code may use that runner.
The runner must preserve the workspace and VM disk privately after the job;
do not let a later checkout or cleanup erase evidence/ownership. Failed and
successful guests are stopped, not deleted. Reusing an existing run ID is refused.

The separate Verify published compact image identities workflow performs
anonymous registry pulls on a hosted runner, selecting runtime or builder
independently. It neither runs containers nor tests packet connectivity. With
the current null manifest it deliberately fails; it is for post-publication
verification, not evidence that publication has occurred.

On 2026-09-24, 270 repository Python tests, Go tests/vet, shell and Ansible
checks passed. A separate same-host VM with an existing local image cache
passed startup, MUP-C rebuild, a real one-call test, rollback, and the full
one-call/baseline/lease/restart/network/neighbor suite. The source-only copy
also passed checks and secret scanning with the existing exact public-test-key
exception. The main lab was not restarted. This was a local regression, not
a new empty-cache build or a registry release test. The new optional CI jobs
have not been dispatched; provisioning their protected runner is still pending.

Later on the same date, the candidate orchestration script was also executed
locally against a newly created, independently named VM on the same physical
host. That guest had no previous lab images or build cache. Source startup,
MUP-C rebuild, one-call, rollback, and the complete packet/recovery suite passed.
The subsequent audit completed for all fourteen immutable images but returned
a policy failure, so the overall candidate command correctly exited nonzero.
Its cleanup stopped the guest and retained its disk, sources and private
evidence. The main lab remained running and passed its final health check.
This was not a GitHub runner job, a different-physical-host test, or a prebuilt
registry-pull acceptance test. See the later [image review](image-distribution.md)
for the remaining distribution blockers. The accompanying source-material
tooling passed 292 repository Python tests and 282 exported-source Python
tests, Go tests/vet, shell/Ansible checks and redacted source secret scanning;
the three ordinary GitHub workflows also passed.

Remaining gates are tracked in [image distribution](image-distribution.md):
current immutable-image re-scan, corresponding sources/notices, native/eBPF
coverage, privacy and key-origin review, independent runtime/builder approval,
signing, and explicit publication authority. Registry publishing automation
remains pending until those gates and the destination repository are settled.
No workflow added here has package-write permission or registry credentials.
After publication, test an empty-image-cache VM on the same physical host,
then edit/rebuild/rollback one component and rerun the packet suite. Offline
tests or an existing-cache regression are not that clean-pull acceptance test.

The identity and deployment-protection mechanisms follow
[Docker digest pulls](https://docs.docker.com/reference/cli/docker/image/pull/)
and [GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).
