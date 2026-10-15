# Issue-driven local development

English | [日本語](issue-development.ja.md)

## Status and authority

This opt-in implementation is separate from the legacy background scheduler.
It does not install or enable a service, restart old timers, copy credentials,
operate the live lab, or publish images. Defaults are disabled and paused.
Passing offline tests does not attest a live Issue/PR/CI/merge cycle.

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

Independent review uses the OpenAI Responses API, with no tools, a fixed schema
and the exact candidate commit/diff/changed files. Configure `review_model` and
`review_api_key_file` explicitly. The key file has the same private-file rules.
An absent reviewer leaves work at `awaiting-review`; it is not replaced by Qwen
self-review or a reused ChatGPT login. API usage is billable. The default limit is
one review call per UTC day, at most 80 KB of input and 3,000 output tokens; reserve
calls before sending, including failed calls. These limits are not a dollar cap:
set the provider project's spending controls as well. No key is requested or
loaded by plan/status or a local trial.

Uncertain findings or insufficient upstream compatibility evidence block approval.
This initial reviewer cannot browse upstream sources or run extra tools; dependency
updates can therefore require a human decision even when the static scope gate allows them.
See [OpenAI automation guidance](https://learn.chatgpt.com/docs/non-interactive-mode)
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
and retains evidence. To retry an inspected unpublished failed/interrupted/rejected
task once, preserving old runs:

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

Automatic merge additionally requires opt-in, the existing conservative background
classification, a current independent review, an unchanged Issue, identical base
and head, and successful `test`, `gitleaks`, `source-and-binaries` checks from
GitHub Actions for that head. Strict required branch checks must be configured;
the runner does not change protection and never requests an administrator bypass.
Merge submits the expected head SHA. Runtime/policy/infrastructure changes, uncertain
dependencies and missing live evidence remain decisions, not automatic merges.

Before enabling unattended publication/merge, verify a real Issue → PR → CI → merge
cycle, cancellation on edits/label removal, notification delivery, duplicate
prevention, timeout/restart recovery and resource isolation on the target host.
Offline mocks and a local Qwen trial do not replace those acceptance steps.
