# BeerShop v9.8 — isolated encrypted export + schema-managed startup

**Only existing staging Draft PR #17. No main merge, deployment to Render,
Neon use, real buyer data, operational passwords, payments, or new paid services.**

## 1. Encrypted HTTP backup rehearsal (CI fixtures only)

`GET /api/admin/v98/maintenance/export-encrypted` is **disabled by default**
and intentionally accepts **ONLY** the named disposable fixture database:

- SQLite basename `beershop_ci_test.db`, or
- local PostgreSQL database `beershop_test`, with a loopback host and
  explicit `BEERSHOP_V91_ALLOW_LOCAL_POSTGRES=1`.

The endpoint requires all of:

```text
BEERSHOP_V98_ENCRYPTED_EXPORT=1
BEERSHOP_V98_EXPORT_ACK=SYNTHETIC_OWNER_MFA_ENCRYPTED_EXPORT_ONLY
BEERSHOP_V91_STAGING_ACK=ISOLATED_STAGING_ONLY
BEERSHOP_V91_ENFORCE_RBAC=1
BEERSHOP_V9_STAFF_AUTH=1
BEERSHOP_V9_MFA_KEY=<private test-only Fernet key>
BEERSHOP_V98_EXPORT_PASSPHRASE=<independent, test-only high-entropy secret 32+ characters>
```

`BEERSHOP_PERSISTENT_DATABASE_URL` must be unset. Never copy actual
secrets into this document, GitHub, messages or CI output. The export
accepts only a valid **owner** MFA-backed staff session, not a shared
admin token or an auditor session. The server produces a v9-integrity
snapshot **in memory**, validates it, then AES-256-GCM encrypts the bytes
using v9.4 scrypt before returning the `.bse` encrypted attachment.
It never responds with plaintext JSON. Headers disable client caches.
Neither the enrollment factors, key nor plaintext are logged by
application code.

When this mode is enabled, the legacy
`GET /api/admin/maintenance/export` endpoint is **explicitly blocked**
even for an authorized owner. When it is disabled, the old endpoint
remains unchanged for compatibility — including its **plaintext export
security risk** in production. This v9.8 rehearsal **does not fix live
production backups**. Do not enable it on production, migrate real
client data into a specially named local fixture, or use real secrets.

To decrypt for disaster-recovery testing, the operator must separately
retain the passphrase, use the v9.4 offline verifier and follow local
retention and key rotation policy. The app does not expose any decrypt
operation online.

## 2. Strict, pre-migrated schema startup rehearsal (CI fixtures only)

`BEERSHOP_V98_SCHEMA_MANAGED=1` explicitly enables a *different*
startup mode when these staging values are also set:

```text
BEERSHOP_V98_SCHEMA_ACK=PREMIGRATED_SYNTHETIC_DB_NO_AUTO_BOOTSTRAP
BEERSHOP_V91_STAGING_ACK=ISOLATED_STAGING_ONLY
BEERSHOP_V91_ENFORCE_RBAC=1
BEERSHOP_V9_STAFF_AUTH=1
```

The mode requires the same explicitly named SQLite/PostgreSQL fixtures
and `BEERSHOP_PERSISTENT_DATABASE_URL` unset. It checks the existing
database schema read-only, including tables, columns, types and key
constraints, and **requires exact `alembic_version=0002_v95`**.
If the DB is unversioned, drifted, remote, misconfigured or mismatched,
startup fails before mutation.

When the preflight succeeds, startup **does not run**:

- `Base.metadata.create_all(engine)`;
- legacy copy/migrate-once;
- default Store/Category seeding;
- CRM and inventory backfill.

This demonstrates a clean migration-managed boot on a synthetic
fixture. **The actual legacy production startup remains unchanged.**
The old `0001_v95` Alembic baseline is still model-metadata driven,
so it is *not* a frozen historical migration; no production update
is authorized.

## 3. Tests and security limitations

New tests verify owner vs worker vs anonymous vs shared token,
encrypted-only refusal of plaintext export, wrong/remote DB safeguards,
encrypted format and post-decryption integrity, tampered archive
detection, missing/wrong Alembic marker, schema drift, safe startup with
zero backfill, and zero table creation on failed boot.

v9.7 NO-GO release gate now tracks both v9.8 capabilities only as
**observed in source**, not as production readiness proof. Main
production blockers remain open: immutable historical Alembic DDL,
independently restored real offsite backups, external security review,
secure ingress, Ukrainian compliance and stock/payment verification.

**PR stays Draft. No production deployment, paid services or real data.**


## 4. Pinned DDL contract (migration-review guard, not executable migration)

The staging CI also produces the SQLite/PostgreSQL model DDL (tables and
indexes) using `app/schema_contract_v98.py`. A schema-only SHA-256 is
pinned in `migrations_v95/schema_v98.lock.json`. CI runs:

```sh
python scripts/schema_contract_v98.py \
  --check-lock migrations_v95/schema_v98.lock.json \
  --output /tmp/beershop-v98-schema-contract.json
```

The artifact `beershop-v98-schema-contract` is retained for 7 days.
The DDL document includes NO buyer, staff, order or payment records.
A changed model now fails the explicit fingerprint check until the
reviewed lock is updated along with a compatible migration. This lock
is model-derived (not a fully frozen historical migration), so it does
**not** certify `0001_v95` for production deployment. Review generated
DDL and SQLite/PostgreSQL compatibility independently before approving
a revised hash. Never silence the guard by auto-updating the lock.
