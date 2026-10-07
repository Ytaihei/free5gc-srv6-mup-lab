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

### Commissioning failures and coordinator repair

Commissioning records separate `worker-auth`, `publisher-auth`, `isolation-probe`,
`worker-workspace` and `source-checks` phases. Each attempt retains a private directory under
`/var/lib/srv6-mup-background/commissioning/`, with a `result.json` and 0600 logs.
An error names the failed phase and exact log path. Inspect logs locally; do not
publish authentication output. Recommissioning pauses the queue and invalidates
earlier acceptance before testing, so failure cannot leave it commissioned.

The `worker-workspace` phase checks directory traversal and scratch-file I/O as
the actual isolated worker, without invoking a model, deploying or publishing.
It uses a new retained worker-owned directory under the same work parent as
development checkouts; its path is recorded in `result.json`.

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
and disable both timers and stop the background service. From the reviewed
checkout run:

```bash
sudo python3 scripts/install-background-development.py --apply --update-coordinator
```

This narrow updater verifies all installed manifest hashes, accepts only the
coordinator administrator/executor/installer, their tests, paired guide and
translation hashes, and refuses source inventory changes. It cannot update the
policy, units, dependencies, tools or lab code. It retains old files and manifests
under the private `updates/` directory, preserves accounts, credentials and task
evidence, then leaves the queue paused and commissioning/automatic merging off.
Run `commission` again before resuming. A partial update retains its backup and
fails closed; do not delete installation/state or edit the manifest to bypass it.

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
