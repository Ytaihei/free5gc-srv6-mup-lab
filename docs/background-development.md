# Optional background development

English | [日本語](background-development.ja.md)

## Status and boundaries

The repository provides a paused, opt-in coordinator, task queue, isolated
worker/checker/publisher execution, immutable PR receipts and a conservative
merge gate. This is a server-side systemd job, not an app Scheduled task.
It uses [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode).
The implementation does not attest that an installation, unattended run or
recovery rehearsal has happened. Consult the private operator state for that.

The agreed schedule is Monday, Wednesday and Friday, 03:00–04:00 in Japan.
New development stops at 03:40. Missed runs are not caught up. Only one run is
allowed; failures retain source and evidence. Three consecutive failures pause
the queue. A interrupted-run record blocks resume until acknowledged.

**Live candidate deployment to both lab profiles and automatic recovery rehearsals
are not enabled by this first coordinator release.**
The queue records required live profiles and does not treat their absence as a
pass. Commissioning refuses to turn on a live profile. These are remaining
implementation/acceptance work, not features made safe merely by a disabled
timer. No lab credentials are provisioned by this installer.

## Inspect without installation

```bash
python3 scripts/background-development.py plan
python3 scripts/background-development.py status
python3 scripts/background-development.py run --dry-run
python3 scripts/install-background-development.py
```

These commands do not initialize state, run Codex, contact GitHub, install
services or touch a lab. An ordinary checkout cannot run or resume the privileged
coordinator. The default local state is under the user's private state directory;
it is not the installed scheduler's state.

## Install and commission

Installation needs sudo and existing trusted Codex and pinned Go installations.
Preview first, then supply their absolute paths:

```bash
sudo python3 scripts/install-background-development.py --apply \
  --codex /absolute/path/to/package/bin/codex --go-root /absolute/path/to/pinned-go
```

The installer creates three dedicated accounts, installs prerequisites and a
root-owned copy under `/opt/srv6-mup-background`, and private coordinator state
under `/var/lib/srv6-mup-background`. Existing accounts, units, installation or
state cause refusal, not replacement. A partial installation is retained; do not
delete it blindly and rerun. Neither timer is enabled.

Supply the complete standalone Codex 0.154.0 Linux x86-64 package, not an isolated
copy of its main executable. The installer checks fixed SHA-256 hashes for
`bin/codex`, `bin/codex-code-mode-host`, `codex-package.json`, `codex-path/rg`,
`codex-resources/bwrap` and `codex-resources/zsh/bin/zsh`. These pins describe the
reviewed trusted package bytes; they are not an upstream signature verification.
All six files enter the private installation manifest and are checked before
development. They are not added to this source-only repository. No automatic
download, version upgrade, model switch or authentication copying is performed.

If unit files were copied and a timer enabled **before installation**, a timer
listing does not demonstrate a working developer. Its executable under `/opt`
may not exist. The installer now checks unit conflicts before installing packages
or creating accounts/state. For this specific recovery, add
`--adopt-existing-units` to the installation command above. This accepts only
byte-identical, root-owned templates with no group/other write access, symlinks,
hardlinks or systemd overrides. Active services and units from other directories
are refused. Matching timers are disabled before installation and remain disabled
until authentication, commissioning and resume have succeeded. Custom units and
partial installations require separate review; do not delete them to force a retry.

Authenticate locally under the new accounts; never copy an existing auth file or
paste tokens into a command line, chat, issue or public workflow:

```bash
sudo -u mup-bg-worker -H /opt/srv6-mup-background/bin/codex login --device-auth
sudo -u mup-bg-publisher -H gh auth login --hostname github.com --git-protocol https
```

Give the publisher credentials restricted to this repository's contents, pull
requests and issue operations, with read access to check results. Do not grant
administration, workflow editing, packages or branch-protection bypass. The
worker has no publisher auth, sudo, Docker/libvirt socket or lab SSH key. Tests
run under a third account with neither credential set. Model usage consumes the
authenticated Codex account's allowance; no API-billing fallback is configured.

Commissioning checks account isolation, authentication, actual model/tool execution
and source checks. Supply
your intended public commit identity:

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py commission \
  --author-name 'Your Name' --author-email 'you@example.org'
```

This leaves the scheduler paused and automatic merging off. The initial model is
the deployment's previously selected Codex model, fixed in the private operator
file; an unavailable model fails rather than silently switching. Do not enable
automatic merging until a real isolated PR/check/review cycle has been inspected.
The operator must explicitly set the private `automatic_merge` field after that
acceptance; a model cannot write this file.

### Commissioning failures and coordinator repair

Commissioning records separate `worker-auth`, `publisher-auth`, `isolation-probe`,
`worker-workspace`, `worker-tools` and `source-checks` phases. Each attempt retains a private directory under
`/var/lib/srv6-mup-background/commissioning/`, with a `result.json` and 0600 logs.
An error names the failed phase and exact log path. Inspect logs locally; do not
publish authentication output. Recommissioning pauses the queue and invalidates
earlier acceptance before testing, so failure cannot leave it commissioned.

The `worker-workspace` phase checks directory traversal and scratch-file I/O as
the actual isolated worker, without invoking a model, deploying or publishing.
It uses a new retained worker-owned directory under the same work parent as
development checkouts; its path is recorded in `result.json`.

The `worker-tools` phase verifies the package and invokes the configured Codex
model inside the same worker isolation, in that new scratch directory, for at
most 180 seconds. It requires a private file containing a fresh challenge, not
just a successful process exit or the model's assertion of success. This uses
the authenticated account's model allowance but does not inspect a development
checkout, deploy, publish or merge. A missing helper, malformed/error event stream,
or absent/wrong artifact prevents commissioning and leaves the queue paused.

A worker failure with `200/CHDIR` can occur if that root-owned work parent was
created as `0700` by the coordinator's `UMask=0077`, despite requesting `0755`
when creating it. Commissioning and development now explicitly set this one
directory to `0755` using a non-symlink directory descriptor. Existing checkouts
and unfinished edits are preserved. Unexpected ownership, links or permissions
that allow other users to write are refused, not adopted. The private worker
home stays `0700`; credentials and evidence permissions are unchanged. Reapply
the reviewed coordinator repair below and recommission to repair an existing
parent and test access before resuming the timers. Do not use recursive chmod
or delete the checkout to work around this failure.

The `source-checks` phase runs as the checks account against a retained,
root-owned snapshot under `/opt/srv6-mup-background/candidates/`. Only files in
the verified public-source inventory are copied and checked against the installed
manifest. Installed tools, private settings and evidence are excluded: running
the archive checks directly in the installation directory would incorrectly
include those extra files. The snapshot path is recorded in `result.json`;
neither success nor failure deletes it. Snapshot preparation failures also retain
a private phase log and cannot commission the installation.

`InaccessiblePaths` denies access but need not hide the path's existence. The
probe tests directory access and Unix socket connections, not `Path.exists()`.
Missing/masked endpoints are accepted; a successful connection or merely a
stopped daemon is not.

`IPAddressDeny` filters packets. A blocked TCP connection can retry until Python
reports `EAGAIN` (`network_errno: 11`), which does not prove isolation. The
private-network probe instead sends one UDP datagram to its own ephemeral port
bound on `127.0.0.2`, without contacting an existing host daemon or a live lab.
Only `EPERM` or `EACCES` from `sendto` counts as denial. Successful sending,
connection refusal, unreachable routes, timeouts and socket setup failures do
not pass. The log identifies this method as `udp-self-send`. No network allowlist
or sandbox restriction is relaxed. See the upstream
[packet-filter documentation](https://github.com/systemd/systemd/blob/v255/man/systemd.resource-control.xml)
and [Python timeout handling](https://github.com/python/cpython/blob/v3.12.3/Modules/socketmodule.c).
Run this probe only in the isolated checks service; ordinary host execution does
not validate that service. This loopback probe does not attest every address
range, IPv6 path or live-lab operation.

To apply a reviewed coordinator repair to an existing installation, first stop
and disable both timers and stop the background service. If a single-run test
is active, stop that service too and inspect/acknowledge any interrupted run
before updating. The updater refuses an active test service. From the reviewed checkout run:

```bash
sudo python3 scripts/install-background-development.py --apply --update-coordinator
```

This narrow updater verifies all installed manifest hashes, accepts only the
coordinator administrator/executor/installer, their tests, paired guide and
translation hashes, and refuses source inventory changes. It cannot update the
policy, units, dependencies, tool versions or lab code. It retains old files and manifests
under the private `updates/` directory, preserves accounts, credentials and task
evidence, then leaves the queue paused and commissioning/automatic merging off.
Run `commission` again before resuming. A partial update retains its backup and
fails closed; do not delete installation/state or edit the manifest to bypass it.

For a legacy installation containing only `bin/codex`, explicitly restore the
missing pinned companions while applying the coordinator repair:

```bash
sudo python3 scripts/install-background-development.py --apply --update-coordinator \
  --codex-package /absolute/path/to/package
```

The existing Codex executable must already match the pinned package. The updater
can add only missing, hash-matched companion files, not replace a different tool
or adopt untracked files from a partial repair. It records additions with the
backup and writes the new installation manifest last. Timers/services must be
stopped as above. Recommission before resuming; `--update-coordinator` without
the package option does not silently install missing tools.

Worker and review stdout are retained as `worker.jsonl` and `review.jsonl`, with
diagnostics separately in `worker.jsonl.stderr.log` and `review.jsonl.stderr.log`.
All are private 0600 evidence. The coordinator rejects malformed/incomplete JSONL,
top-level errors, failed turns and error items even if the process exits zero or
later emits `turn.completed`. Such worker execution errors fail the trial and
immediately pause the queue; do not treat an error-free conversation ending as
proof that a task was accomplished. Older mixed logs remain untouched and must
not be fed directly to a JSONL parser. See the
[official output/event documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

After repairing a legacy no-change trial, an operator may explicitly retry that
exact unpublished code-only task while the queue is paused:

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py retry-task \
  --task documentation-maintenance --run EXACT_RUN_ID
```

This requires matching retained trial evidence with outcome `needs-decision` and
no PR or publication receipt. It preserves the checkout, base, edits and original
logs/results, records the retry and prioritizes that retained work; it does not
reset failure counts or resume the queue. It refuses active work, live tasks,
published candidates, mismatched evidence and repeat use of the same trial ID.
After successful commissioning and this explicit retry, use `resume` and
`test-once`; inspect the new evidence before accepting the development cycle.

## Run, pause and observe

After commissioning, an operator may resume **code-only** development and enable
the two timers. This does not complete the still-disabled live-lab portion:

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-development.py \
  --state /var/lib/srv6-mup-background resume
sudo systemctl enable --now srv6-mup-background.timer srv6-mup-background-watchdog.timer
sudo systemctl list-timers srv6-mup-background.timer
```

Verify the actual installation as well as both timers:

```bash
test -x /opt/srv6-mup-background/venv/bin/python3
getent passwd mup-bg-worker mup-bg-checks mup-bg-publisher
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-development.py \
  --state /var/lib/srv6-mup-background status
systemctl is-active srv6-mup-background.timer srv6-mup-background-watchdog.timer
sudo journalctl -u srv6-mup-background.service -u srv6-mup-background-watchdog.service
```

The status command in an ordinary checkout uses a different, initially paused
state; it cannot prove commissioning. A timer waiting for its first run, a
service with no execution history, and passing offline tests are not evidence of
an unattended development cycle. Keep automatic merging off until an actual
isolated candidate, checks, review, PR and notification have been inspected.

Starting the regular service manually still enforces the approved time window.
The explicit code-only single-run mode below is separate; neither mode permits
daytime live deployment. The worker receives one task; tests and an
independent read-only review use the frozen candidate. The publisher opens a PR
with a fixed, sanitized body. Resume after interrupted publication reuses the
recorded branch/commit instead of opening duplicate PRs. At most three automation
PRs may be open; unrelated Dependabot or contributor PRs are never auto-merged.

```bash
sudo systemctl disable --now srv6-mup-background.timer
sudo systemctl stop srv6-mup-background.service
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-development.py \
  --state /var/lib/srv6-mup-background pause
sudo journalctl -u srv6-mup-background.service
```

The watchdog retains an interrupted run and pauses future work. After inspecting
the retained run and ensuring the worker has stopped, acknowledge its exact ID:

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py acknowledge --run EXACT_RUN_ID
```

Acknowledgement preserves all source/evidence and leaves scheduling paused.
Read logs locally; they are not sanitized publication artifacts. One GitHub issue
receives a weekly queue summary and urgent failure notices, using only fixed task
IDs/statuses. Notification delivery still needs acceptance; GitHub outages retain
a pending notice locally. There is no automatic cleanup, email or app-inbox
notification. Until delivery is verified, check service state and PRs manually.

## Immediate single-run test (code only)

After installation, commissioning and `resume`, an operator can exercise one
queued task immediately without editing the Mon/Wed/Fri timers or the policy:

```bash
sudo /opt/srv6-mup-background/venv/bin/python3 \
  /opt/srv6-mup-background/scripts/background-admin.py test-once
sudo journalctl -u srv6-mup-background-test.service -f
```

This is a real development run, not a dry run: it uses the configured model
account and can create a public PR and update the fixed GitHub status issue.
Only the calendar gate is replaced by a deadline 40 minutes after execution
starts. The transient service has a 45-minute runtime limit and a 60-second stop
timeout. Installed-source checks, commissioning, authentication, resource limits,
failure limits, the shared lock, credential-free checks, independent review and
publication checks remain in effect. A paused queue, interrupted run, no ready
task, a selected task requiring live profiles or three open automation PRs blocks
the trial. The PR limit also applies to resumed work. Automatic merging is forced
off in memory, without modifying operator settings; live operations stay off.

The launcher returns after service startup, **not after successful completion**.
Concurrent tests use the same service name and cannot replace a running test.
Scheduled development and the test use the same lock. The command changes no
timer, persistent override, queue priority or model. `_execute-test-once` is an
internal service entry point, not an operator command.

The journal prints a private result path under
`/var/lib/srv6-mup-background/trials/RUN_ID/result.json`. Inspect that exact file
locally with sudo. It records the start/end times, fixed deadlines, last phase,
task outcome, PR number when present, and final notification delivery. A
`completed` result means the selected pipeline returned and its final status
issue update succeeded; it does **not** attest asynchronous GitHub CI, a merge or
live-lab validation. A retained publication can be resumed without rerunning the
worker, and a no-change task and its trial both end as `needs-decision`, not
`completed`, without a PR. Older results are not retroactively rewritten. Those outcomes
are not evidence of a fresh end-to-end candidate/check/review/PR cycle. Inspect
the retained run evidence and exact PR checks before accepting that cycle.

The final status report bypasses weekly deduplication but publishes only fixed
task IDs/statuses, not private logs. A notification failure is recorded as a
failed trial with notification pending, preserving any already-created PR. To
stop a running test:

```bash
sudo systemctl stop srv6-mup-background-test.service
```

Stopping or timing out invokes the watchdog. An interrupted active run is kept
and the queue is paused until operator inspection and exact-ID acknowledgement.
An abrupt stop can leave `status: running` in the result file; that is incomplete
evidence, never success. Child jobs retain their existing execution limits. Do
not delete evidence or force-clear locks to retry. The journal and result are
private operational evidence, not publication artifacts. Offline tests of this
mode do not substitute for an actual isolated trial on the installed host.

## Merge rules and remaining acceptance

Only small README/glossary prose changes and strictly additive unit tests of
existing behavior can pass the first automatic gate. Other document changes,
test edits/deletions, runtime changes, dependencies, policy, licenses, executable
examples and claims of standard compliance or successful validation require a
human decision. Public inventory additions are limited to new test files; paired
documentation hashes are mechanically regenerated, never treated as review.

The gate requires an immutable separate review, all local checks and successful
GitHub checks named `test`, `gitleaks` and `source-and-binaries` from GitHub Actions
for the same commit. Main must still equal the recorded base. Merge uses the
expected head SHA, squash and existing protection, never an admin bypass. A stale
base, inconclusive review or changed head stops automatic merging.

Remaining acceptance is: actual root installation and sandbox/network checks;
account authentication; a real PR/CI/merge cycle; notification delivery; both live
adapters and timed rollback rehearsals. Until those steps succeed, this is a
tested code coordinator foundation, **not a fully commissioned autonomous lab
developer**. Image distribution, OS/DB upgrades, VM deletion and publication of
raw captures stay out of scope.
