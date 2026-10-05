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
  --codex /absolute/path/to/codex --go-root /absolute/path/to/pinned-go
```

The installer creates three dedicated accounts, installs prerequisites and a
root-owned copy under `/opt/srv6-mup-background`, and private coordinator state
under `/var/lib/srv6-mup-background`. Existing accounts, units, installation or
state cause refusal, not replacement. A partial installation is retained; do not
delete it blindly and rerun. Neither timer is enabled.

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

Commissioning checks account isolation, authentication and source checks. Supply
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

Starting the service manually still enforces the approved time window. There is
no daytime deployment bypass. The worker receives one task; tests and an
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
