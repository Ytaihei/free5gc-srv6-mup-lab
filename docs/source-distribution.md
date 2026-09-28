# Source-only distribution

English | [日本語](source-distribution.ja.md)

The public product is an isolated IPv4 Direct SRv6 MUP experimental lab.
Publishing its source does not authorize exposing a running controller,
dashboard, free5GC WebUI or subscriber database to the internet.

## Explicit boundary

`config/public-source.json` lists every public file exactly. Newly tracked or
nonignored files must be classified before export. The policy is loaded from
the same immutable commit as the source; uncommitted policy edits cannot change
an existing commit's candidate. Public and private path selections cannot overlap.

The candidate retains the active Go implementation, Vinbero patch, Ansible and
libvirt provisioning, example YAML, tests, CI, reproducibility instructions,
LICENSE and THIRD_PARTY_NOTICES. It excludes:

- Host-specific gaming, disk recovery, firewall and backup operations.
- Inactive VPP/Python-sidecar implementations and their dedicated tests.
- Private work estimates, approval records, PR/CI links and detailed run logs.
- Git history and metadata, runtime disks/images/binaries, unreviewed captures, generated
  cloud-init, local configuration and credentials.

Excluded recovery files are preserved privately, not deleted. Generic lab
addresses, public example UE material and intentional upstream/author
attribution remain; they are not production credentials. See
[SECURITY.md](../SECURITY.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).
This workflow is a packaging/privacy gate, not a full vulnerability audit or
legal certification. Binary/image/service distribution needs a separate review.

The only binary exception is the [reviewed PCAP example](../examples/pcap/one-call/README.md).
`config/public-pcaps.json` fixes each permitted path, SHA256, size and packet count.
It is loaded from the same commit as the source, including during history checks.
Tree/export/history gates reject missing, unlisted, changed, malformed or truncated
captures. Only bounded classic Ethernet PCAP is supported; PCAPNG is not admitted.
Binary identity checks inspect raw strings and packed IPs, not every protocol's
decoded fields. Offline packet-level privacy review is required before updating
the inventory; a matching digest freezes that review, not publication approval.
No ordinary runtime capture directory or private capture tooling is distributed.

## Inspect and export

```bash
make check-public-source
# Keep real identifiers in a private JSON string array outside the public tree.
python3 scripts/export-source.py --check-tree . \
  --denylist /private/path/publication-identities.json
python3 scripts/export-source.py --revision HEAD \
  --denylist /private/path/publication-identities.json \
  --output artifacts/source/candidate-unique.tar.gz
```

Review and commit source before creating a release candidate. Export uses only
committed blobs; it never includes uncommitted changes. It never overwrites an
archive, pushes refs, changes visibility or approves publication. JSON output
records the revision, file count, SHA256 and `publication_approved=false`.
Tar metadata uses zero timestamps/UID/GID and empty owner names; Git commit
PAX headers are omitted. Identical selected source and modes produce identical
archive bytes, independent of commit identity.

The private denylist must include the operator's known host/user names, Tailnet
identifiers and any other personal values requiring redaction. It is optional
for portable CI but required for the operator's final privacy review. The
checker examines every selected file, including tests and web assets, matches
case-insensitively and also inspects Python constant string concatenation.
Errors disclose paths, not matched values. Do not embed the real denylist in
public tests. This targeted check does not detect every encoded or unknown
identifier; manual review and secret scanning remain necessary.

Extract into a new empty directory, enter `free5gc-srv6-mup-lab`, and run the inventory
and privacy checks **before** tools create any files there:

```bash
python3 scripts/export-source.py --check-tree . \
  --denylist /private/path/publication-identities.json
./scripts/verification-tool.sh gitleaks dir . --redact --config .gitleaks.toml
make check
make lint
ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml --syntax-check
```

Run Gitleaks from the extracted project directory so its exact public test-key
allowlist paths match. No actual private key or token is permitted. The archive
inventory check rejects extra files, including excluded private paths. Preserve
the digest and sanitized check results outside the extracted source directory.

## Publication remains a separate approval

Use a new history-free repository or approved source archive. Do not switch a
recovery repository with old host data to public and do not push its historical
refs. Issues, PRs, Actions logs, artifacts, releases and screenshots are separate
publication surfaces and are not sanitized by this exporter.

Before publishing, approve the exact candidate and destination; reconcile Go
module/import paths, repository links, CODEOWNERS and reporting contacts with
that destination. Enable and test a private vulnerability-reporting channel and
required CI checks. Existing repository-owner attribution is intentional, not
an anonymity promise. Public release and any runtime-artifact distribution
require explicit approval beyond successful local checks.

## Inspect complete Git history separately

An exported tree passing checks does not approve publishing the recovery Git
repository. The following read-only check intentionally fails on a repository
whose reachable history contains private files, even after those files have
been removed from its current branch:

```bash
python3 scripts/export-source.py --check-history . \
  --denylist /private/path/publication-identities.json
```

It checks local refs and HEAD, all reachable committed trees against their
public inventories, annotated tag chains, commit/ref/tag metadata and forbidden
file formats. Identity checks include paths and Python constant concatenation.
Shallow repositories, grafts and replace refs are refused. Its diagnostics show
only issue categories/counts, not matched private values. A failed history
check exits nonzero; it never deletes branches or rewrites history. It does not
fetch refs, inspect reflogs/unreachable objects, or inspect GitHub metadata and
cached PR refs. Gitleaks remains a separate gate.

The private recovery repository is expected to fail this check. CI tests the
positive case by initializing an isolated repository from the exported public
tree with a synthetic verification identity; that is not a real release or
approved author identity. Use the [publication checklist](publication-checklist.md)
for the destination, branch policy, remote metadata and final approval gates.
