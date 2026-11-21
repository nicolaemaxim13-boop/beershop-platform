# BeerShop v9.7 — Production-readiness NO-GO audit

**Scope:** Existing Draft PR #17, staging only. **No main merge,
no production deployment, no access to customer data, orders, payment
providers, actual Neon databases or private credentials.**

## Result: NO_GO (not certified ready for real sales)

The new read-only source audit deliberately returns
`production_ready: false` and `decision: "NO_GO"`. Passing CI means
the checks passed on synthetic SQLite/PostgreSQL and browser fixtures;
it is **not** authorization to activate payments, accept actual orders
or move live data.

### Read-only report

Owner or auditor (MFA when strict staging RBAC is enabled) can inspect:

`GET /api/admin/v97/release-gate`

The endpoint never exports customer rows or secrets. It reads a fixed
set of repository source files to check whether safeguards remain in
the code. A static match only means **observed in source**, not proven
deployed or independently security-reviewed.

Offline report from the checkout (no DB or external services):

```sh
python scripts/release_audit_v97.py \
  --assert-no-go --assert-staging-controls \
  --output /tmp/beershop-v97-release-gate.json
```

Creates a **new 0600 JSON report without overwriting** an existing
review. Do not use this CLI to certify production readiness.

The GitHub CI test job executes this check after Python tests and
uploads the sanitized report as the temporary artifact
`beershop-v97-no-go-audit`, retained for 7 days.
CI fails if an expected staging safeguard disappears or if the source
gate incorrectly claims production is ready. The test does not call
Render, Neon or payment APIs.

## Explicitly unresolved critical blocks

1. **Versioned migrations:** `app/main.py` still uses
   `Base.metadata.create_all(engine)` at boot. The initial Alembic
   revision still reads current SQLAlchemy model metadata; it is
   not an independently frozen historical DDL migration.
2. **Server backup privacy:** The existing
   `/api/admin/maintenance/export` produces plaintext JSON potentially
   containing customer personal data. Local test encryption is not a
   server-side encrypted offsite backup service.
3. **Recovery:** Although an AES-GCM-to-disposable-PostgreSQL restore
   was tested, there is no approved production-like recovery drill on a
   separately isolated Neon test environment, with verified retention,
   key escrow, recovery time and monitored rollback.
4. **Network and security:** Rate-limit and signed-edge logic is
   staging-only. A trusted proxy, external edge/WAF protections,
   hardened ingress, observability, pen testing, key rotation and an
   incident response plan have not been certified or deployed.
5. **Real commerce (Ukraine):** Seller identity, Ukrainian fiscal and
   consumer requirements, refunds, public offer, privacy disclosures,
   ownership of IBAN and live bank reconciliation remain unverified.
6. **Stock and operations:** The database's virtual inventory is not
   proof of physical goods or supplier commitments. Stocktake and
   shipment/returns procedures remain unverified.
7. **Go-live governance:** Owner sign-off, staged release plan,
   rollback criteria, incident contacts and independent technical
   authorization are not available.

This audit does not self-approve even if environment variables are set
to staging mode or if all local tests pass.

## Production approval checklist (must be completed outside this PR)

- Review all critical blockers with evidence; maintain a written,
  time-stamped approval log and the identities/roles of reviewers.
- Replace metadata-based bootstrap with controlled immutable migration
  scripts and test a safe upgrade/rollback on an independently isolated
  replica of the relevant schema.
- Run encrypted, monitored offsite backups, verify retention and
  restoration with separate, revocable credentials and a recovery plan.
- Validate ingress, edge and MFA configuration under independent
  security review with abuse, load and rollback testing.
- Complete Ukrainian merchant, privacy, stock and payment readiness
  with responsible human professionals.
- Only after that, propose a **separate** production release with an
  explicit owner approval and current CI evidence.

**Do not convert Draft PR #17 into a production release based solely
on this report.** It is an accountable blocker inventory, not a
production certificate.
