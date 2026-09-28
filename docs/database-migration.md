# Compact database upgrades

[日本語](database-migration.ja.md)

The single-VM profile uses pinned MongoDB 8.0.32 for a fresh database and
requires an AVX-capable x86-64 guest. The six-VM reference keeps its existing
image lock. An existing compact MongoDB 4.4 volume is retained on normal
startup; changing a version lock is not a database migration.

## Commands

Use the same profile for every command. These operations interrupt the lab.

```sh
./lab database status
./lab database upgrade
./lab test all
./lab health
```

The upgrade supports the lab's unauthenticated standalone MongoDB 4.4 with
FCV 4.4. Authentication, replication, capped collections, views, time-series
and system collections in application databases require a separate migration.
Application data is not replaced by subscriber fixtures to claim preservation.

The command downloads fixed images before downtime, stops writers and the
database, checks volume ownership and consumers, and cold-copies the data
to a new volume. Only the clone is upgraded through 4.4, 5.0, 6.0, 7.0 and
8.0. Intermediate servers have no application network and have TTL deletion
disabled. Each stage checks the server/FCV version, collection validation,
BSON-aware document hashes/counts, indexes and collection options. A single
fixed client performs comparisons; redundant legacy index namespace metadata
is excluded. Internal database contents are not asserted byte-identical:
FCV intentionally changes server metadata.

The new selection is activated only after all comparisons pass. WebUI login,
subscriber reconciliation preserving SQN, real UE registration and ICMP/HTTP
traffic are then checked. The source volume, cloned volume, stage containers
and private migration evidence are retained. No volume cleanup is automatic.

## Interruption and recovery

```sh
./lab database recover
```

An unfinished journal blocks ordinary startup, builds and component deployment.
Before activation, recovery starts the original database. Once activation may
have exposed the new database to writes, recovery resumes the new volume;
it does not silently discard newer data. Failed recovery keeps the journal.
This command is not an arbitrary point-in-time rollback command.

Keep the private selection and migration records under the guest's
`.lab/runtime/database/` together with the retained volumes. If the selection
record is lost while a modern volume or migration evidence remains, startup
refuses to guess either a legacy volume or a new empty database. Restore the
reviewed record matching the intended image and volume; do not delete volumes
to bypass this check.

Restoring the old backup after successful activation would lose later writes
and requires a separate explicit operator decision. Never run old MongoDB
binaries against the upgraded volume. The migration does not claim general
backup coverage or production database support.

## Verification and limits

The 2026-09-23 isolated lab migration from 4.4.30 to 8.0.32 passed every
stage's application-data comparison and real registration/traffic gates.
Focused tests cover persistent selection, missing-selection ambiguity,
ownership, cold source mounts, failure journals and both recovery phases.
An independent review found the missing-selection fallback issue; regression
tests now cover both retained legacy data and fresh modern installations.

Different-physical-machine clean installation is excluded from the current
acceptance scope by operator decision, not recorded as passed. Research-feature
backlog items remain pending. Image redistribution is a separate review; see
[image distribution](image-distribution.md).

The staged path follows the [MongoDB 8.0 upgrade instructions](https://www.mongodb.com/docs/v8.0/release-notes/8.0-upgrade-standalone/)
and [MongoDB 7.0 upgrade instructions](https://www.mongodb.com/docs/v7.0/release-notes/7.0-upgrade-standalone/).
They do not authorize a direct binary downgrade or replace application tests.
