# Repository publication checklist

English | [日本語](publication-checklist.ja.md)

This checklist separates a reviewed public source candidate from a GitHub
repository approved for publication. Passing source, dependency and secret
checks is not a full vulnerability audit, legal certification or approval to
expose running lab services.

## Current publication state: 2026-10-01 (JST)

The source repository [Ytaihei/free5gc-srv6-mup-lab](https://github.com/Ytaihei/free5gc-srv6-mup-lab)
is public, with `main` as its integration branch and
[taihei@sfc.wide.ad.jp](mailto:taihei@sfc.wide.ad.jp) as the contact. Source
publication is no longer awaiting the repository-name/contact decision.
The reviewed example PCAPs are the explicit exception already described below;
this is not approval to distribute arbitrary captures or private evidence.

Runtime/NF images, builders and VM images are **not distributed or approved**.
The bundled `config/compact-release.json` remains `release: null`; use the
[source-build workflow](prebuilt-development.md) and keep the separate
[image-distribution gates](image-distribution.md) pending. A source publication
does not authorize exposing a running lab or publishing recovery history.

The checklist remains guidance for future publication changes. The dated
2026-09-28 handoff below is a **pre-publication historical record**, not the
current source-publication task list. Preserve its original counts, results
and decision boundaries rather than rewriting the past as a later audit.

## Keep the recovery history private

- Preserve the existing recovery repository privately. Its private files,
  branches, author metadata and old commits are not part of the public product.
- Removing a merged branch does not remove its commits from the default
  branch. Creating another branch from that history is not a clean release.
- Start a separate repository from the reviewed, history-free source export.
  Do not fork, mirror, copy the old `.git` directory, or push old refs into it.
  Do not use `git push --all` or `git push --mirror` for publication.
- Merged recovery branches can be deleted as optional housekeeping after
  checking their unique commits and PRs. Deletion is a separate deliberate
  operation, not a prerequisite for exporting source or a privacy guarantee.

[GitHub's visibility documentation](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
explains that public visibility also exposes Actions history/logs.
[GitHub's sensitive-data guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
also describes residual PR references and cached views. A branch cleanup alone
does not sanitize these surfaces.

## Source and artifact gates

- Freeze an exact reviewed commit and export only `config/public-source.json`.
  Run the external private-identity denylist, Gitleaks and extracted-tree checks
  in [source distribution](source-distribution.md). Scan file paths as well as contents.
- Retain the authoritative LICENSE, upstream attribution and third-party notices.
  Public sample addresses/UE credentials are intentional lab fixtures, not
  production credentials. Owner/module attribution is intentional and must not
  be removed indiscriminately as if it were a secret.
- Keep local overrides, SSH material, subscriber databases, unreviewed PCAPs, screenshots,
  raw dashboard JSON, private run logs and VM/container/binary exports outside Git.
  Even a lab-only example capture contains identifiers, authentication exchanges,
  timestamps and virtual MACs; review and approve that artifact separately.
  Only [reviewed examples](../examples/pcap/one-call/README.md) fixed by
  `config/public-pcaps.json` and the source inventory may be committed/distributed.
  Replacing bytes or changing a digest requires renewed packet-level review.
- Review both English and Japanese documents, linked destinations, image metadata,
  filenames and author/committer/tagger identities. Hashes do not prove translation
  quality or detect unknown/encoded private information.
- In the newly initialized public-source repository, check all available local
  refs with `--check-history`, not just the current files. Use a complete clone
  and separately compare remote branches/tags/PR refs; the tool does not fetch them.
- Keep the exact candidate digest and check results privately until the release
  record itself is reviewed. Do not copy private audit reports into the public tree.

## Destination and GitHub gates

- Approve the destination owner/name and candidate digest before any publication.
  If the name changes, reconcile Go module/import paths, issue-form links,
  CODEOWNERS, documentation, CI and reporting contacts, then rerun checks.
- Use `main` as the public integration branch, short-lived feature/docs branches,
  PRs and successful CI. Configure rules requiring the `test`, `gitleaks` and
  `source-and-binaries` checks, blocking deletion/force-push of the integration
  branch, and review requirements appropriate to the actual maintainer team.
  Verify enforcement after the visibility change as well. Prefer automatic
  deletion of merged public working branches; keep reviewed release tags.
- Verify a working private vulnerability-reporting channel. The current
  issue-form URL is not proof that it is enabled. GitHub's native facility is
  for public repositories; a 404 on a private repository does not establish its
  future state. See [GitHub's reporting configuration](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository).
- Review repository description/homepage, Issue/PR bodies and comments, Wiki,
  Discussions, Pages, deployments, Actions logs/artifacts/caches, releases and
  attachments. Do not transfer the recovery repository's existing records.
  Disable unused public surfaces, or populate them only with reviewed material.
- Keep Actions permissions minimal, checkout credentials unpersisted and
  artifact retention bounded. Do not put actual private identifiers in public
  CI variables, reports, screenshots or test fixtures.
- Once publishing, develop the public lab code in the public repository and
  keep machine-specific operations and private evidence separately. Do not
  merge recovery history back to synchronize changes.

## Required decision record

Before a new publication, the owner must record the destination, exact source
digest, reviewed author/contact identities, source/license/privacy check results,
remote-ref/metadata review, CI/ruleset verification and final explicit approval.
Record any unavailable checks as unverified, not passed. Runtime service exposure
and binary/image/PCAP distribution require their own scope and approval.

<a id="decision-handoff-2026-09-28-jst"></a>

## Historical pre-publication handoff: 2026-09-28 (JST)

| Deliverable | Technical disposition | Still required |
| --- | --- | --- |
| History-free source export | Candidate for source-only publication after final extracted-tree checks | Owner/destination, author/contact review, exact digest and explicit approval; new repository settings/CI verification |
| Runtime/NF images | Not approved, despite zero HIGH/CRITICAL scanner candidates in the latest set | Corresponding-source/license review, remaining key-pattern provenance, release-path tests and publication controls |
| Optional builder and upstream database | NO-GO for redistribution | Advisory review/remediation plus the separate distribution gates; keep the DB upstream-only |
| Live lab, private evidence and recovery history | Not part of the public source candidate | Separate deliberate authorization; keep private |

The completed offline source review covered all 241 files of a frozen exported
candidate. It found one medium-severity, high-confidence command-injection path
in six-VM test automation: a controller session key was interpolated into SSH
shell text. Private management-plane access and a later operator test were
required. The final source replaces all six affected suppress/resume calls with
a shared argument-quoting helper, including cleanup, and adds special-character
round-trip and call-site regressions. The frozen review still records the
finding; the patch and its test evidence are separate, not a retroactive
zero-finding audit. No live exploit was sent to the lab.

The isolated compact candidate passed the full packet/recovery suite and
component rebuild/1call/rollback checks. The main lab remained running; the
candidate VM was stopped with its state retained. A second physical host was
not tested by explicit scope choice. Full upstream dependency implementation,
the live deployment's security and private recovery history were outside the
offline source review. See [image distribution](image-distribution.md) for
artifact-bound counts and unresolved image gates, including the conditional
prebuilt dashboard socket-access bug. Plain first-time `./lab up` with the
bundled `release: null` is not a released prebuilt installation path; source
builds remain the available route.

Keep the final export hash, source revision, successful CI run IDs, scan report,
patch-verification logs and exceptions in the private decision record. Final
packaging checks must bind the patched committed bytes, not the earlier frozen
scan archive. A local one-commit repository initialized with synthetic test
identity can validate packaging/history checks; it does not approve the real
author identity or the public destination. No repository/image publication or
visibility change is authorized by this checklist.
