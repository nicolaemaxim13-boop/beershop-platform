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
