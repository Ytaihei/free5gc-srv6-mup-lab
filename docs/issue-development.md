# Issue-driven local development

English | [日本語](issue-development.ja.md)

## Status and authority

This opt-in implementation is separate from the legacy background scheduler.
It does not install or enable a service, restart old timers, copy credentials,
operate the live lab, or publish images. Defaults are disabled and paused.
Passing offline tests does not attest a live Issue/PR/CI/merge cycle.

Review now uses ChatGPT-plan OAuth only. Start with the separate
[connection pilot](issue-chatgpt-pilot.md) and keep the coordinator disabled until
the local review and eventual Issue/PR acceptance have been inspected.

Ask Codex to create an Issue with a goal, allowed paths and acceptance criteria.
Backlog creation alone does not authorize execution. An explicit request to start
development may add the ready label. Both the Issue author and the actor on the
latest ready-label event must be in the operator's trusted-user list. Public
contributors, comments and generated text cannot grant execution authority.

The pipeline is: authorized Issue → local Qwen worker → isolated checks → separate
review → PR → required CI → optional bounded merge. The computer must be running;
polling recovers available work after downtime, but interrupted development needs
explicit inspection and retry. It is not a native Codex subagent or app automation.

## Task contract

An Issue body contains exactly one block like this (Codex can write it for you):

```agent-task
{
  "goal": "Add a focused regression test for an existing input-validation rule.",
  "paths": ["tests/"],
  "acceptance": "Preserve all existing assertions and production behavior; run the unit checks."
}
```

An operator adds the label `agent:ready` only to an authorized task. Removing the
label, closing the Issue or changing its title/body invalidates pending work before
publication/merge. Removing and reapplying the label creates a new task generation;
it is not an automatic retry. Comments do not expand the task. Only one generation
should be authorized at a time; stale generations are cancelled on the next poll.

The worker may edit scoped files under `cmd/`, `internal/`, `tests/`, `docs/`, or
the explicitly allowed README and Go dependency files. It cannot edit the runner,
CI, instructions, Makefile, credentials or host configuration. New inventory paths
are restricted to unit-test files; larger features need a separately reviewed
change to this contract. This is a deliberately bounded first version.

## Worker and checks

The adapter calls the existing local Ollama endpoint with `qwen3.5:9b-16k` and
provides only three tools: `read_file`, `apply_patch` (exact single-occurrence text
replacement), and `run_tests`. It does not give the model a shell, GitHub token or
arbitrary command execution. This is a small tool loop, not a launch of the full
Codex CLI used in the earlier feasibility experiment.

The adapter disables thinking output to avoid empty completions when reasoning
alone consumes the output budget. Response counts/reasons remain in private logs.
Model requests are limited to 16K context, 4,096 generated tokens per request,
24 turns and 600 seconds by default. The controller bounds prompt size, file size,
candidate size, daily job count and pending candidates. There is no model fallback
or automatic model download. A model's success statement is not acceptance.

Tests run without network or credentials in bubblewrap, using a temporary copy of
the candidate, read-only toolchains/caches and no host home, Docker/libvirt socket
or lab state. No sandbox failure triggers an unsandboxed fallback. The worker's
unit command is fixed. The independent final gate runs `make check`, `make lint`,
the Ansible syntax check and Gitleaks. Dependency and scanner caches must already
contain the pinned materials; missing offline materials fail rather than opening
network access. The parent regenerates inventory/translation hashes mechanically;
that is not a semantic translation review.

The adapter itself runs as the operator user. Filesystem/network isolation applies
to candidate execution, not to every process belonging to that user. Other trusted
processes under the same account can modify configuration and state. Do not use
this as a multi-user hostile-code hosting service. The supplied user unit adds
memory, CPU and process-count bounds; a manually launched trial has timeout and
per-process limits but not that complete cgroup resource boundary.

## Configure without enabling

Prerequisites: Linux, Python with the repository development dependencies, Git,
bubblewrap with working unprivileged namespaces, pinned Go, offline Go module and
verification-tool caches, and the existing local Ollama/model. No root is needed
for normal commands once these prerequisites exist.

Copy `config/issue-development.example.json` to a private operator location and
set its absolute state/toolchain/cache paths and public commit identity. Keep
operator configuration and all credentials outside the repository and candidate
directories. The state directory must be owned by the operator and mode `0700`.

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json plan
python3 scripts/issue-development.py --config /absolute/private/operator.json status
```

The `plan` command performs no remote or model operations. The `status` command
initializes private state if absent, but never starts development. The three
independent configuration gates are `enabled`, `publish`, and `automatic_merge`.
Neither enabling one nor adding an Issue label enables the others.

Use a repository-scoped GitHub token in an operator-owned `0600` regular file,
configured through `github_token_file`. Grant only the required Issue, content,
PR and check-reading access, plus the read access needed to inspect branch rules;
do not grant protection bypass. No token is put in command arguments or worker
environments. Publishing uses a trusted Git credential adapter outside the checks.

Alternatively, after explicit operator approval for unattended use of an existing
GitHub CLI login, set `github_auth_user` to that account's login and
`github_token_file` to `null`. The default is `null` (token-file mode); older v2
configurations without this key keep that default. This uses `/usr/bin/gh auth token`
for the fixed `github.com` host and selected account, with the operator's existing
`~/.config/gh` store/keyring. It does not copy the credential or narrow its scopes:
a broad existing login remains broad. A repository-scoped token is still preferred.
Environment token overrides, alternate gh configuration directories, account
fallback and interactive login are not used. Missing/expired credentials stop the
operation. Only the trusted API/Git parent uses the credential; it is not passed
to the Qwen worker, candidate tests, command arguments or retained logs. The Git
helper receives only the account selector. Service activation is still separate.

Version 2 configuration replaces the paid API key with `review_session_dir`, the
dedicated pilot OAuth directory (outside source and coordinator state), and
`review_included_only_confirmed`. Select `review_model` from that account's model
catalog. Old version 1/API-key settings are rejected, not silently migrated.
Never copy an unrelated Codex login. There is no Platform-key or paid fallback.
Before confirming included-only use, verify that paid-credit consumption cannot
occur and automatic purchases are off. For a manual trial, a confirmed zero credit
balance with automatic additions disabled is also an operator-confirmed boundary;
it is not an indefinitely enforced account spending cap. Recheck after account changes.

The tool-free reviewer receives the immutable commit, task, diff and full changed
files and returns the existing strict SHA-bound JSON schema. Malformed, conflicting,
refused or incomplete output cannot approve. It uses streaming array input and
requires a successful completion event, including when text arrives only as deltas.
After successful isolated checks, the coordinator supplies the commands, observed
exit codes and private-log SHA-256 hashes bound to the candidate head. Raw logs
and host paths are not sent. These observations are not proof of test coverage;
missing evidence stays missing and the reviewer must not invent a successful run.
Limits are one reserved call per UTC day by default (including failures), 80 KB
input, 64 KB response text, 1 MiB stream and an elapsed-time check. These client
limits are not a server token or monetary cap; unsupported `max_output_tokens`
is not sent. plan/status and the Qwen-only trial do not load reviewer credentials.

The same private-directory lock as login serializes refresh and inference. Near
expiry the reviewer refreshes the issued client session and atomically stores the
rotated token. A pending rotation after a crash or network ambiguity blocks reuse
until sign-in; confirmed unusable refresh tokens are cleared, retaining registration.
New refresh ID tokens are signature/issuer/audience/subject checked when returned.
Temporary errors never trigger another billing path or silent model substitution.

Quota/auth/provider failures leave jobs at `awaiting-review` and set a shared
`review_hold`; polling does not repeatedly call the reviewer. Inspect the fixed
diagnostic in status/private `review-wait.json`, resolve the issue, and explicitly
run `review-resume`. This clears only the review hold, not daily counts or the
development pause. It does not assume a quota-reset time or re-run Qwen.

Uncertain findings or insufficient upstream compatibility evidence block approval.
This initial reviewer cannot browse upstream sources or run extra tools; dependency
updates can therefore require a human decision even when the static scope gate allows them.
See [ChatGPT-plan sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Trial, run and stop

First exercise a local immutable snapshot with a JSON task file matching the block
above. This uses Qwen and real checks, but never calls GitHub or the reviewer:

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json trial \
  --source /absolute/path/to/checkout --revision FULL_40_CHARACTER_COMMIT \
  --task /absolute/private/task.json
```

Evidence remains under the private state directory even on failure. A successful
trial ends at `awaiting-review`, not at merged. Inspect the diff and check logs.
To review an existing local Qwen candidate with all three switches still off:

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json review-local \
  --source /absolute/private/trial/repository --base FULL_BASE_SHA --head FULL_HEAD_SHA \
  --task /absolute/private/task.json
```

This clones the local immutable candidate, re-runs credential-free isolated checks,
then performs one real subscription review under the shared daily budget. It never
calls GitHub, re-runs Qwen, publishes or creates a mergeable job. Evidence is retained
under `reviews/`; only `review-approved` exits successfully. A `review-rejected`
or `awaiting-review` outcome is not approval. A successful local review does not
attest the remote Issue/PR/CI/merge cycle.
Use repeatable `--context RELATIVE_PATH` options to include up to ten additional
files from that same commit's public-source inventory (for example, the source
under test). They share the 80 KB input limit. No host files or mutable working
tree content is sent. Without enough evidence the reviewer must request human review.

To run the actual Issue flow, provision credentials, set `enabled` explicitly, and:

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json resume
python3 scripts/issue-development.py --config /absolute/private/operator.json once
python3 scripts/issue-development.py --config /absolute/private/operator.json status
```

Only after an inspected single cycle should a trusted source copy be installed at
the path in `configs/systemd/srv6-mup-issues.service`, the operator configuration
placed at the corresponding private path, and that template installed as a user
unit. It polls only authorized Issues, not a calendar development backlog:

```bash
systemctl --user daemon-reload
systemctl --user enable --now srv6-mup-issues.service
systemctl --user stop srv6-mup-issues.service
python3 scripts/issue-development.py --config /absolute/private/operator.json pause
```

The source tree used by the service must be a reviewed copy outside generated
workspaces. It is not self-updated from PRs. These commands do not install that
copy/unit or configure login-independent user-service lifetime for you.
The legacy `srv6-mup-background.timer` and `srv6-mup-background-watchdog.timer`
are neither used nor enabled.

Stop the service to interrupt active work. `pause` prevents future work after it
obtains the state lock; it does not kill an active worker. Failure pauses the queue
and retains evidence.

After a restart, any task left in preparing, working, checking or reviewing is
marked interrupted and pauses the entire queue before intake or other queued work.
Notification failure does not unpause recovery. Inspect the retained evidence;
explicit retry and resume remain separate operator actions.

To retry an inspected unpublished failed/interrupted/rejected task once,
preserving old runs:

```bash
python3 scripts/issue-development.py --config /absolute/private/operator.json retry JOB_ID
python3 scripts/issue-development.py --config /absolute/private/operator.json resume
```

There are at most two attempts per task generation. Retries need an explicit
operator action; they are not silently scheduled. Review feedback can guide the
new attempt, but old check/review approvals do not transfer. Publication retries
reuse the frozen branch and discover an already-created PR instead of rerunning
the worker. No cleanup deletes failed work. A configuration change to the state
location requires stopping and restarting the service.

## Publication and merge gates

Publication is disabled by default and requires passing final checks and an
independent approval tied to the immutable head. Public notices contain fixed
statuses and PR numbers, never model summaries, raw exceptions or private logs.
Notification failures retain a pending flag for retry. Local status remains
authoritative when GitHub cannot be reached.

All PR change categories are eligible for automatic merge, including authentication,
execution control, runtime code, policy, infrastructure, licenses and dependencies.
Category alone never requires human approval. The Issue classifier retains the
10-file/300-line bounds but no longer uses the legacy scheduler's category allowlist.
Eligibility is not approval: unresolved findings, uncertain compatibility or missing
required evidence still block merge. The review schema's `requires_human` flag is
retained for unresolved questions, not mandatory category-based approval.

Automatic merge additionally requires opt-in, a current independent review, an unchanged Issue, identical base
and head, and successful `test`, `gitleaks`, `source-and-binaries` checks from
GitHub Actions for that head. Strict required branch checks must be configured;
the runner does not change protection and never requests an administrator bypass.
Merge submits the expected head SHA. Previously rejected receipts are not promoted;
changed candidates need fresh checks and review. No review/CI gate is bypassed.

This is merge authorization, not broader worker or deployment authority. The bounded
Qwen worker still cannot edit runner/CI/instruction files or add arbitrary new source
paths. Such PRs may be prepared through a separately authorized workflow and merged
by an agent after review/checks, without a category-based human approval step.
The Issue coordinator only merges its own recorded PRs; it does not scan and merge
arbitrary external PRs. Service activation, credential use and installation updates
remain separately authorized. The legacy scheduler is not re-enabled or migrated.

Before enabling unattended publication/merge, verify a real Issue → PR → CI → merge
cycle, cancellation on edits/label removal, notification delivery, duplicate
prevention, timeout/restart recovery and resource isolation on the target host.
Offline mocks and a local Qwen trial do not replace those acceptance steps.
