# Contributing

English | [日本語](CONTRIBUTING.ja.md)

## Development workflow

Create a focused branch and keep functional changes separate from generated
files, dependency locks, and documentation-only changes. Run these checks
before opening a pull request:

These repository checks need host Go, Ansible, Python YAML/Jinja2 and Make;
`./lab deps` prepares compact deployment, not all contributor tooling. See
[host setup](docs/operations.md) and [check coverage](docs/supply-chain.md).
Do not provision six VMs just to run the offline checks.

    make check
    ansible-playbook -i ansible/inventory/lab-inventory ansible/site.yml --syntax-check
    git diff --check

End-to-end tests disrupt the lab UE. Choose the commands for the deployment you
own, run them serially, and state the profile and tests in the pull request.
Documentation-only checks do not imply these forwarding tests were rerun.

For the **single-VM compact profile**, see the [hands-on guide](docs/hands-on.md).
Use the original checkout/profile; an explicit configuration precedes every
subcommand as `./lab --config /absolute/profile.yml`. For the default profile:

    ./lab health
    ./lab test all

For the **six-VM reference**, six provisioned guests are required:

    make test-baseline
    MUP_ENABLE=1 make test-mup
    MUP_ENABLE=1 make test-lease
    ONE_CALL_ENABLE=1 make test-one-call

## Configuration and secrets

Commit only example lab values. Never commit private keys, access tokens,
Tailnet-specific configuration, production subscriber material, generated
cloud-init files, unreviewed packet captures, VM images, or backup archives.
Only [reviewed example captures](examples/pcap/one-call/README.md) may enter Git:
they require packet-level review, bilingual documentation, exact entries in
`config/public-pcaps.json` and the public-source inventory, and exact ignore
exceptions. A changed digest requires a new review, not automatic approval.

New source files must be classified in `config/public-source.json`. Public
files use an exact allowlist; private-only paths are excluded from candidates.
Run `make check-public-source` and follow
[source distribution](docs/source-distribution.md), including a private,
external identity denylist before approving any archive. Tests must use
synthetic identities, never the operator's actual identifiers.

Before publication, use the [repository publication checklist](docs/publication-checklist.md).
Merged-branch deletion is housekeeping, not history sanitization. Public work
uses short-lived PR branches from the public repository's `main`; never merge
or mirror the private recovery repository's history into it.

## Dependency changes

Pin source dependencies to immutable commits or digests and record their
license. A dependency update must pass the local checks and the baseline/MUP
test suite before its lock entry is changed.

## Generated code

The generated Connect and protobuf Go files are checked in so provisioning
does not require code-generation tools. When the schema changes, regenerate
them with the pinned toolchain and include both the schema and generated diff
in the same pull request.

## Documentation languages

Keep English and Japanese (`*.ja.md`) documents together. Translate every
section without changing commands, sample values, limitations or approval
boundaries. Code blocks remain verbatim; explain them in Japanese outside the
block. Keep the authoritative `LICENSE` unchanged and label its Japanese
translation unofficial. Issue forms retain the same field IDs and validations.

`config/documentation.json` records public pairs and reviewed SHA256 values
for both languages. Private pairs stay in `docs/documentation-private.json`,
which is excluded from public exports. Update both texts, links and hashes
after reviewing a change; do not update hashes just to bypass the check.
`make check` detects missing translations, stale hashes, changed code examples,
broken local anchors/links and publication-scope mismatches. These mechanical
checks do not replace a review of translation meaning.
