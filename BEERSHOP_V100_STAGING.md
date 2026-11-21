# BeerShop v10.0 — Safe Existing-Schema Adoption Review (staging-only)

**Status: NO_GO / Draft PR #17 / do not merge or deploy.**
This v10.0 phase is a **read-only diagnostic and migration planning tool**;
it is not an Alembic adoption runner, data migration, or a real restore.

## Code

- `app/adoption_plan_v100.py`: report on synthetic existing schemas.
- `scripts/adoption_plan_v100.py`: non-success-returning CLI.
- `tests/test_adoption_plan_v100.py`: local SQLite fixture tests for
  version states, indexes, structural drift, remote refusals, sanitized
  reporting and CLI behavior.

The report builds upon the v9.6 deep table/column/type/PK/FK/unique-constraint
inspection; it adds **explicit index** name, order, column list and uniqueness
comparison and checks `alembic_version` read-only.

Versions are classified as `UNVERSIONED`,
`EXPECTED_STAGING_REVISION`, `UNRECOGNIZED_OR_AMBIGUOUS` or
`UNREADABLE_VERSION`. Multiple versions are rejected as ambiguous.
Expression indexes, partial indexes, opaque reflection details and any
different index inventory require manual investigation.

The report includes **no row values, passwords, DB URLs, customer data or
backup payloads**. It never issues `ALTER`, `CREATE`, `DROP`, `UPDATE`,
`DELETE`, `INSERT` or `alembic stamp`, and always returns
`decision=NO_GO` and `writes_to_database=0` (reporting invariant).
Even an exact match yields `candidate_for_manual_review`, **not approval**.

## Offline operation

Use only a disposable synthetic SQLite fixture named
`/tmp/<private-folder>/beershop-v96-preflight-*.db` (mode 0600 and
private parent), or exact allowlisted localhost PostgreSQL
`beershop_v96_preflight` with its explicit local fixture opt-in.
v9.6 target validation forbids remote hosts, Neon/Render, unknown DB
names, missing ACK and any `DATABASE_URL`/
`BEERSHOP_PERSISTENT_DATABASE_URL` configuration.

Example using a newly created **fake-data** local fixture ONLY:

```sh
env -u DATABASE_URL -u BEERSHOP_PERSISTENT_DATABASE_URL \
  BEERSHOP_V96_PREFLIGHT_ACK=ISOLATED_READONLY_PREFLIGHT \
  BEERSHOP_V96_PREFLIGHT_URL=sqlite:////tmp/<private-folder>/beershop-v96-preflight-demo.db \
  python scripts/adoption_plan_v100.py
```

**Exit 3 is intentional**: it indicates a valid read-only NO_GO report.
Exit 2 indicates target refusal or failure. Never treat a 3 as safe
to migrate, stamp or deploy. The script does not accept URLs as CLI
arguments to reduce accidental shell-history disclosure.

## Limits / required gates for a real existing DB

- Snapshot/backup must be separately encrypted, recoverable, monitored,
  access-controlled and independently restored; no live backup is used.
- Verify row-level referential integrity, unique constraints involving
  business rules, currencies, inventory, pending orders, PII handling,
  schema ownership, nonstandard indexes, triggers, extensions, RLS and
  third-party writers with explicit owner consent.
- Review lock/downtime impact on the exact production DB version; produce
  a repeatable forward/rollback migration sequence in an isolated clone.
- Independently verify source DDL hash against *actual* historical
  migrations and an accepted schema baseline; NEVER auto-stamp.
- Real payment integration, mobile purchase flow, legal/fiscal/privacy
  checks, reconciliation and key custody remain independent NO_GO gates.

## Verification status

The previous v9.9 revision passed both CI jobs on 2026-11-15. The newer
P1 and v10 GitHub runs (Nov 16 and Nov 20) have failed **before a runner
was allocated**: `runner_name=""`, `steps=[]`, near-instant job
failure, no step logs. The exact GitHub account-level reason is unknown.
New v10 changes are NOT CI-validated. No billing settings or paid runner
changes are approved. Re-run the existing GitHub CI only once Actions
eligibility is restored, with no bypass or production release.

For the operator: inspect GitHub account Actions usage/limits, repository
Actions policy, and GitHub Support if necessary. Do not conflate failure
to allocate a runner with failing pytest assertions.

The next safe engineering task is a synthetic, independently tested DR
roundtrip and a rollback/cutover rehearsal checklist. See
`BEERSHOP_ROADMAP.md`.
