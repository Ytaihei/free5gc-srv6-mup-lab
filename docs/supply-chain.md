# Quality and supply-chain checks

English | [日本語](supply-chain.ja.md)

## Run locally

```bash
make check
make lint
make supply-chain
./scripts/verification-tool.sh gitleaks git --redact --config .gitleaks.toml --log-opts='--all' .
```

The scanner executables are downloaded from exact upstream releases and their
archive SHA-256 values are verified on every invocation. No root installation
or remote shell installer is used. GitHub Actions dependencies use commit SHAs.
The secret scan runs directly with the locked Gitleaks version and does not
require giving a scanner a GitHub token.

## Coverage and gating

Pull requests and main/master pushes run unit/syntax/vet checks, ShellCheck,
full-history secret scanning, and the supply-chain job. The latter scans:

1. `go.mod` (including indirect modules) and pinned `requirements-dev.txt`;
2. freshly compiled MUP-C, observer, mupctl and dashboard executables, including
   the Go standard library embedded in those artifacts;
3. dependency license identifiers, with version-specific metadata supplements
   where Trivy cannot extract licenses from requirements or Go binary metadata.

HIGH and CRITICAL CVEs fail CI, including findings without a published fix.
Unknown/unreviewed package licenses and empty package scans also fail. There
is no blanket CVE suppression. `config/supply-chain-policy.yml` records the
reviewed identifiers and upstream links for supplemental license metadata.
This is an engineering review gate for **source-only** distribution, not a
legal determination or approval to ship third-party binary artifacts.

Container images use a separate classification policy and material checklist;
see [image distribution review](image-distribution.md). GPL/LGPL recognition
there generates corresponding-source requirements, not a blanket ban or approval.

License matching for binaries uses the same package **and version** in the
source inventory. Raw scanner JSON is retained separately from the
license-enriched JSON used to generate CycloneDX SBOMs. Original vulnerabilities
are not removed. Reports live under ignored `artifacts/supply-chain/`; CI
uploads only JSON reports (never executable files), even on failure, for 14 days.

The source job does not install Python's transitive dependency environment.
It does not scan guest OS packages, the patched Vinbero tree, gtp5g/UERANSIM
binaries, cloud disks or contents of the digest-locked free5GC/Mongo containers.
Those runtime artifacts require separate scans before any binary/image release.
The lock manifest and THIRD_PARTY_NOTICES are inventories, not substitutes for
such scans. A green source CI does not mean the entire running lab is CVE-free.

The workflows declare a weekly schedule and Dependabot updates. Each repository
owner must enable the required services and configure required-check branch
rules; checked-in YAML alone does not establish those repository settings.

## 2026-09-05 remediation

The initial new gate detected HIGH advisories in Go 1.25.5, `x/net` 0.55.0,
`x/text` 0.37.0 and gRPC 1.82.1. The toolchain was updated within the same Go
minor line to checksum-pinned 1.25.14; the modules were updated to 0.56.0,
0.39.0 and 1.83.1 respectively, with the resolved module graph in `go.sum`.
The source and four freshly built tools then passed the HIGH/CRITICAL and
license gate. This result is tied to the scan date/database; rerun regularly.

## Upstream references

For the coordinated Go 1.26.8/PFCP 1.1.2 update and GoBGP 4.9/4.8
interoperability gate, see [the validation summary](validation-summary.md).

- [Trivy filesystem CLI and scanner flags](https://trivy.dev/docs/v0.74/guide/references/configuration/cli/trivy_filesystem/)
- [Trivy license scanner](https://www.trivy.dev/docs/latest/scanner/license/)
- [Trivy 0.74.0 release](https://github.com/aquasecurity/trivy/releases/tag/v0.74.0)
- [Go release metadata and archive checksums](https://go.dev/dl/?mode=json&include=all)

## 2026-09-09 pre-merge rescan

The refreshed database rejected gRPC 1.83.1 in both the module inventory and
the built MUP controller for HIGH CVE-2026-84445 (GHSA-2v4p-qf9q-27wj, published
2026-09-08 UTC). The gate was not bypassed. The module was advanced to the
same-line patched release 1.83.2, with its required dependency graph resolved
in `go.mod`/`go.sum`. See the [upstream advisory](https://github.com/advisories/GHSA-2v4p-qf9q-27wj)
and [release notes](https://github.com/grpc/grpc-go/releases/tag/v1.83.2).

The advisory concerns gRPC-Go xDS server handling of requests lacking both
authority headers. A package/binary finding does not by itself establish
exploitability of this lab's in-process GoBGP use. This is a source dependency
update; it does not replace running guest binaries. Dependency checks and a
full clean-OS forwarding validation are separate evidence levels.

## Publication boundary

Use the [source distribution workflow](source-distribution.md) for a local,
history-free candidate. The exact allowlist excludes host operations, inactive
implementations, private development records and runtime artifacts. Review the
extracted candidate, not just the working directory. Passing secret-pattern
detection alone is not a privacy or license audit and does not authorize release
or broaden the runtime-artifact scan coverage.
