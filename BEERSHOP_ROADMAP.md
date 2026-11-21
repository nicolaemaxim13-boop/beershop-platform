# BeerShop — autonomous staging engineering queue

**Scope:** `nicolaemaxim13-boop/beershop-platform`, existing Draft PR #17,
`staging/beershop-v9-sales-readiness-audit`. This file is the *task queue*,
NOT a license to deploy to `main`, production Render/Neon, payments or
any real customer data. No paid service or KeyCRM is needed.

At each automated review: read the latest branch SHA, PR and CI status,
select **one safe step** from the queue, implement or report the blocker,
write tests, update the task status and link the commit. Do not say CI
passed if GitHub runners could not start. Never auto-merge. Production
release remains **NO_GO** independently of tests.

## Current highest-priority blockers

- [ ] **P0/CI runner provisioning:** GitHub Actions jobs started failing
  before runner assignment from Nov 16 onward (empty runner name, zero
  steps, no job logs). Owner must inspect free GitHub Actions usage /
  repository policy or request GitHub Support. Do **not** turn on paid
  services or disable checks. When available, rerun existing full CI;
  only then mark P1/v10 changes CI-verified.
- [x] **P0/v9.9 baseline DDL frozen:** 24-table Alembic initial revision,
  checksummed SQLite/PostgreSQL DDL; still fixture-only.
- [x] **P0/v9.9 isolated key rotation:** AES-GCM encrypted synthetic archive
  rotation; original remains, no production secrets touched.
- [x] **P0/v10.0 read-only existing-schema adoption planning:** indexes,
  version states, type/constraint drift, synthetic target guards, NO_GO
  report, negative tests. **Local standalone smoke tests passed (2/2)**
  before upload; full integrated tests pending GitHub runner recovery.
- [ ] **P0/v10.1 disaster recovery rehearsal (SQLite regression tests staged; full CI pending):** add
  deterministic end-to-end synthetic encrypted backup -> authenticated
  restore into a brand-new disposable test database -> row counts,
  important invariants and schema checksum verification; negative cases
  for tampering, missing tables, wrong key, already-existing target and
  recovery failure. Six synthetic-only tests now cover authenticated SQLite restore,
  manifest/row digest and schema checksum, tamper/wrong key, missing table,
  existing target and non-temp destination, plus AES-GCM-authenticated but invalid schema manifests and missing-table payloads. PostgreSQL end-to-end drill
  and independent CI validation remain pending. DO NOT use or modify any live service.
- [ ] **P0/v10.2 existing DB baseline adoption design:** publish
  change/rollback runbook for manual DBA review. Never auto-stamp an
  unversioned database, never execute against real databases.
- [ ] **P0/v10.3 static secure export audit:** identify every legacy
  plaintext HTTP export path and document safe opt-out/fail-closed
  staging migration, with tests; do not alter production behavior without
  separate explicit permission.
- [ ] **P0/operator security review:** independent tests for MFA,
  sessions/revocation, ingress trust, rate limiting, backups, legal and
  payment controls. Requires non-code evidence, not automatically cleared.
- [ ] **P1/mobile checkout and anonymous funnel:** newest browser +
  rapid-submit tests need runner verification; recheck server quote and
  synthetic order flow. No live payment service.
- [ ] **P1/Ukraine merchant release checklist:** identity, inventory,
  UAH FX rounding, IBAN ownership/reconciliation, receipts, returns,
  privacy, consent and real user disclosures. Owner/legal review needed.

## Completion criteria

Each checked engineering item should have a reproducible diff, negative
tests and evidence. **Working code, green CI and a security audit are
three different things**. Without live-independent proof, the release
decision remains `NO_GO`; no production action is implied.

Task cadence is controlled by the ChatGPT automation, not GitHub Actions.
Its schedule may evaluate hourly; this does not mean execution runs
continuously or guarantees code can be changed when an app is unavailable.

### 2026-11-20 staging verification note

- Added two negative v10.1 disaster-recovery tests (commit `c8eab107a0a1bf0bf9599618ee4f723868357d44`): correctly authenticated encrypted archives containing an invalid schema checksum or missing required table must fail before a destination file exists.
- Current branch-head GitHub Actions run `44765379383` failed with both jobs reporting no steps/logs, so these tests are **not CI-verified**. Do not claim a green CI or promote this branch.
- **Next safe step:** wire an independently named, disposable local PostgreSQL v10.1 encrypted restore fixture and verify row/manifest/schema invariants plus repeat-restore rejection; do not reuse an existing database or access Neon.

### 2026-11-20 v10.1 PostgreSQL negative-test increment

- Added two tests to `tests/test_pg_encrypted_restore_v96.py` proving AES-GCM-authenticated but invalid schema digests or missing required tables are rejected **before attempting a PostgreSQL connection**. They use synthetic payloads and a fake loopback URL, never Neon or a real database.
- GitHub Actions run `44776125698` on the prior head failed before runner assignment (both jobs `steps=null`). The new tests are **not verified by hosted CI**; no green CI claim is made.
- **Next:** independently run these tests in a disposable environment, then add row-count/schema invariants to the already isolated PostgreSQL v9.6 restore fixture and validate with recovered CI. Never create or reuse a real database.
- Release remains **NO_GO**, PR Draft, production unchanged.

### 2026-11-20 v10.1 authenticated row-integrity regression

- Added synthetic negative PostgreSQL restore test: re-encrypting a modified store row under the valid password must still fail the application snapshot digest **before any database connection**. Commit `26418c4e4c850e48b56464459210060c4f984f84`.
- Previous head CI run `44785029755` failed with both jobs reporting `steps=null`, before test execution. This change is **not CI-verified**; keep NO_GO.
- **Next:** independently execute the PostgreSQL fixture and strengthen successful restore verification with per-table row counts and schema fingerprint, without connecting to any persistent or remote database. CI runner provisioning remains P0.

### 2026-11-20 v10.1 PostgreSQL full-table verification increment

- Expanded the existing **opt-in disposable PostgreSQL restore** test to compare **every table's actual restored row count** with the AES-GCM-authenticated snapshot manifest, including empty tables. It also asserts the full table-name set, total rows and expected metadata schema digest. This is fixture verification, not a production restore certification.
- Added a negative test: a re-encrypted archive with forged per-table counts must be rejected during manifest verification **before a database connection**.
- Commits: `ea20069918a4ad198b14eee7901c79984ebae4d6`, `6cc460b21186e13f4df16165d96317258b298a50`.
- Last checked CI for previous branch head: [run 44794148549](https://github.com/nicolaemaxim13-boop/beershop-platform/actions/runs/44794148549) failed before runner assignment; both jobs have `steps=null` and no logs. **New changes not CI-verified.**
- **Next:** once GitHub Actions runner provisioning is restored, run the complete disposable PostgreSQL restore fixture and inspect any actual assertion failures; separately consider read-only PostgreSQL schema/constraint fingerprint checks. Do not change live databases or weaken checks.
- Release remains **NO_GO**; Draft PR only.

### 2026-11-20 v10.1 restored PostgreSQL foreign-key contract

- Added a read-only foreign-key metadata comparison after the existing disposable PostgreSQL encrypted restore test: constrained columns, referenced tables and referenced columns must match SQLAlchemy metadata for every table. Negative unit test simulates missing foreign keys and requires a hard failure. Code commit `9c69b287ff7e2bf6c57a2f235a3671685e2080d0`.
- Prior branch HEAD GitHub Actions [run 44804450141](https://github.com/nicolaemaxim13-boop/beershop-platform/actions/runs/44804450141) failed before test execution (`steps=null` in both jobs). New code has not passed hosted CI. No production or persistent database was touched.
- **Next:** recover GitHub Actions runner provisioning, run complete isolated PostgreSQL restore tests and investigate actual failures; do not treat a static contract assertion as a live recovery certification. Release remains **NO_GO**.

### 2026-11-20 v10.1 unexpected PostgreSQL foreign-key drift regression

- Added a synthetic negative inspector test that reproduces the expected SQLAlchemy foreign-key contract and injects an **extra** constraint into `stores`. The existing comparison must reject unexpected constraints, not only missing ones. Code commit `d0bd2ae27ae844aa6ca509bb529d7b27e7e5f950`.
- Previous branch HEAD GitHub Actions [run 44812765471](https://github.com/nicolaemaxim13-boop/beershop-platform/actions/runs/44812765471) reported `failure`; the new commit has **not** been verified by hosted CI. Investigate job provisioning and execute the full synthetic PostgreSQL suite when runners are available.
- **Next safe step:** check PostgreSQL restored unique/check constraints against metadata using an isolated inspector fixture, with negative drift tests; do not connect to persistent databases. Release remains **NO_GO**.

- [x] **v10.1 PostgreSQL UNIQUE constraint drift test (2026-11-20):** isolated restore integration now compares PostgreSQL UNIQUE column tuples with SQLAlchemy metadata and adds missing/extra constraint negative cases. Commit `2aaf36b82faeb6f9004088388df849a87eef74a6`. CI not yet verified: last checked HEAD `c19ed384` failed in GitHub Actions run `44822408000`. Next: add CHECK constraint comparison and synthetic negative tests; confirm the actual PostgreSQL restore test once runners provision. Release remains **NO_GO**.

- [x] **v10.1 PostgreSQL CHECK drift regression (2026-11-21):** added strict comparison of explicit CHECK expressions after disposable PostgreSQL encrypted restore, plus synthetic negative tests for missing/extra constraints and a whitespace-only positive case. Code commit `5da1d51d1201997b7271df6d162523389c339c3a`. The comparison intentionally fails closed on PostgreSQL expression rewrites pending manual review. Previous HEAD CI run [44830883591](https://github.com/nicolaemaxim13-boop/beershop-platform/actions/runs/44830883591) failed; new code is NOT hosted-CI verified. **Next:** recover runner provisioning and run actual PostgreSQL integration; inspect CHECK expression normalization against PostgreSQL output before treating restore checks as passing. NO_GO; no production changes.

### 2026-11-21 v10.1 CHECK normalization regression fix

- Fixed a concrete test-helper bug: `r"\\s+"` matched a literal backslash plus `s` instead of whitespace. The inspector CHECK comparison now uses `r"\s+"` so multiple spaces, tabs and line breaks normalize as documented. Strengthened the positive synthetic test with mixed whitespace and added a negative test for a changed comparison operator. Code commit `357088a44edb9e2a32b74637b53a18d3cf909fb4`.
- Previous HEAD Actions run [44838571630](https://github.com/nicolaemaxim13-boop/beershop-platform/actions/runs/44838571630) failed in both jobs; no verified passing CI for this fix. **Next:** check new run jobs and execute PostgreSQL restore on a disposable DB once runner provisioning works; inspect PostgreSQL expression rewrites without relaxing fail-closed comparisons. Production release remains **NO_GO**.

### 2026-11-21 v10.1 CHECK literal case safety

- Fixed PostgreSQL restore CHECK comparison to preserve case, because lowercasing entire SQL expressions can conceal a changed case-sensitive string literal (e.g. 'PAID' vs 'paid'). Whitespace-only normalization remains; identifier case rewrites fail closed for manual review. Added negative synthetic test for changed literal case and adjusted whitespace-only positive fixture. Code commit `902cfe1bcfbffbf24bde987b537c97d7683d7773`.
- At start of this run, Actions [44845278302](https://github.com/nicolaemaxim13-boop/beershop-platform/actions/runs/44845278302) on previous HEAD failed with both jobs `steps=null`, no runner logs. New tests are NOT CI-verified. Do not bypass checks.
- **Next:** restore hosted runner provisioning, execute isolated PostgreSQL integration, then assess actual PostgreSQL CHECK expression rewrites. Keep Draft PR and release **NO_GO**.
