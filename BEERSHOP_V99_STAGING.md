# BeerShop v9.9 — Frozen Fixture Migrations & Backup Key Rotation

**Scope:** Draft PR #17 on `staging/beershop-v9-sales-readiness-audit`.
**NO production merge, Render deploy, Neon access, real database writes,
real payments, customer exports or paid services.**

## 1. Frozen initial Alembic DDL

The previous `migrations_v95/versions/0001_isolated_baseline.py`
called `Base.metadata.create_all()` and therefore depended on the
**current** models. It now loads **only** the checked-in immutable
`migrations_v95/frozen_0001_v99.json` schema snapshot and verifies
its pinned SHA-256 **before executing any SQL**.

- Snapshot inventory: 24 baseline tables, 46 DDL statements in each of
  SQLite and PostgreSQL, including tables and indexes.
- `0002_v95` remains the explicit, additive rate-counter migration,
  producing 25 model tables in the final synthetic CI schema.
- `0001_v95` has **no imports of application model classes** and does not
  compile tables from current metadata.
- The migration refuses nonempty databases before DDL, unknown dialects,
  missing or altered snapshots, and any SQL outside its allowlist of
  `CREATE TABLE` and `CREATE INDEX`.
- Existing `migrations_v95/env.py` still restricts all Alembic upgrades
  to expressly named, disposable localhost databases with an exact
  staging acknowledgement. Never run on a real account or Neon.
- Tests run the same two revisions on disposable SQLite and PostgreSQL;
  the frozen DDL hash, format, rowless DDL and tampering refusal are
  independently tested.

This makes the **staging initial migration immutable**. It does
**NOT** make the production schema safe to stamp. Existing databases
require a separate, reviewed **baseline adoption/migration strategy**,
schema/data compatibility verification, lock analysis, rollback testing,
backup and operator sign-off.

## 2. Offline encrypted backup key rotation (synthetic-only)

`app/backup_rotation_v99.py` and `scripts/rotate_backup_v99.py`
perform a key-rotation drill **only on private**
`beershop-v99-synthetic-*.bse` files under the OS temp directory.

Required:

```text
BEERSHOP_V99_ROTATION_ACK=LOCAL_SYNTHETIC_KEY_ROTATION_ONLY
DATABASE_URL must be unset
BEERSHOP_PERSISTENT_DATABASE_URL must be unset
```

Example **with synthetic fixture files only**:

```sh
env -u DATABASE_URL -u BEERSHOP_PERSISTENT_DATABASE_URL \
  BEERSHOP_V99_ROTATION_ACK=LOCAL_SYNTHETIC_KEY_ROTATION_ONLY \
  python scripts/rotate_backup_v99.py \
    /tmp/private/beershop-v99-synthetic-old.bse \
    /tmp/private/beershop-v99-synthetic-new.bse
```

The CLI prompts invisibly for the old passphrase and two entries of a
**different** new passphrase; neither is accepted as a CLI parameter or
environment variable. The source directory must be private and source
permissions `0600`. The code decrypts only in memory, verifies the
manifest, re-encrypts with random salt and nonce, self-checks the new
passphrase, and writes the replacement to a NEW `0600` file.
The original is preserved for rollback and is **not silently deleted**.
No plaintext JSON is written on disk. A failed passphrase, tampered
ciphertext, missing ACK, live DB env, symlink, unsafe file permissions
or existing destination causes refusal.

**Operationally important:** keeping the old archive until an operator
confirms successful restoration is safer than automatically deleting
the only rollback point. However, the old key remains sensitive while
that archive exists. Real key rotation requires controlled dual-key
custody, expiry, encryption-at-rest policies, audit logs, offsite storage,
retention and disaster recovery tests; this code alone is not enough.

## 3. Production NO_GO remains

The v9.7 release audit recognizes these v9.9 capabilities only as
**observed in source**. Its production decision remains `NO_GO`
and the protected status endpoint is unchanged.

Remaining blockers include:

- Production still runs legacy startup `create_all` by default.
- Safe onboarding of **existing** databases, real change management,
  frozen follow-up migrations and rehearsal rollback remain pending.
- Production plaintext server export still exists, despite the isolated
  owner-MFA encrypted endpoint available only in synthetic test mode.
- No independent production-like Neon restore, real offsite backup,
  disaster-recovery runbook, owner-approved escrow or key rotation.
- Edge/WAF, banking, consumer law, Ukrainian fiscal requirements,
  supplier inventory and privacy checks require real-world review.

Keep PR #17 **Draft** until separate production criteria are met.
