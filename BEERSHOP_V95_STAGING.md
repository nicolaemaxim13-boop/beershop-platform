# BeerShop v9.5 — isolated security and operations pilot

**Scope:** Draft PR #17 only. NO main merge, NO Render or Neon modification,
NO real customers, payments, stock, finance, secrets or paid services.

## 1. Database-backed API request limits

`BEERSHOP_V95_RATE_LIMIT=1` enables the limiter only after:

- `BEERSHOP_V91_STAGING_ACK=ISOLATED_STAGING_ONLY`
- `BEERSHOP_V95_RATE_KEY` is a **separately generated private value, 32+ bytes**
- DB is SQLite local, or explicit allowed loopback PostgreSQL staging
- `BEERSHOP_PERSISTENT_DATABASE_URL` is **unset**

Guard is executed **before** any startup migrations or writes.
If the guard fails, startup returns 503 and never changes the database.
Feature is **off by default**. It is not enabled on production.

Fixed 60-second, HMAC-pseudonymized, database-atomic quotas per ASGI peer:

| Zone | Requests / 60 seconds |
| --- | ---: |
| POST /api/admin/v9/staff/login | 12 |
| Administrative GET / HEAD | 90 |
| Administrative writes | 20 |
| POST /api/orders (checkout) | 15 |

429 returns `Retry-After` and `Cache-Control: no-store`. Untrusted
`X-Forwarded-For` and `X-Real-IP` are **not** used: a proxy must provide
validated ASGI peer identity, otherwise all traffic behind the same peer
will share a quota. This is only **defense in depth**, not an edge
rate-limiting/WAF product; the table will also need time-based pruning,
load testing, alerts and operator review.

## 2. Operator-managed encrypted backup cadence (synthetic local data)

The new `scripts/auto_backup_v95.py` works **exclusively** with a
`beershop-v95-synthetic-*.db` SQLite fixture under the OS temporary
directory. It is not allowed to connect to PostgreSQL, Neon or Render.
Both `DATABASE_URL` and `BEERSHOP_PERSISTENT_DATABASE_URL` must be unset.

Required:

- Synthetic SQLite file must be mode **0600**, within a private temp directory
- Different private archive directory mode **0700**
- Passphrase stored in a separate **0600** file, *outside archive directory*
  (never put this key in the repository or CI logs)
- `BEERSHOP_V95_SYNTHETIC_BACKUP_ACK=I_CONFIRM_ISOLATED_SYNTHETIC_SQLITE_ONLY`

Run one backup (suitable for a PRIVATE local cron/systemd timer):

```sh
env -u DATABASE_URL -u BEERSHOP_PERSISTENT_DATABASE_URL \
  BEERSHOP_V95_SYNTHETIC_BACKUP_ACK=I_CONFIRM_ISOLATED_SYNTHETIC_SQLITE_ONLY \
  python scripts/auto_backup_v95.py \
    --source /tmp/private-fixture/beershop-v95-synthetic-demo.db \
    --destination /tmp/private-fixture/archives \
    --key-file /tmp/private-fixture/encryption-key --keep-days 14
```

A self-managed **foreground** timer can also be started with
`--interval-minutes 60` (minimum 60, maximum 1440), subject to external
service supervision. It is NOT currently deployed or running on Render.
No cron/systemd schedule is installed by this PR.

Implementation uses SQLite's consistent snapshot backup API to an
**in-memory** DB, creates v9-integrity JSON in RAM, encrypts with v9.4
AES-256-GCM/scrypt, immediately decrypts for self-check, and writes
**ciphertext only**, with O_EXCL and 0600 permissions. A lock prevents
overlapping jobs. Retention (7–365 days) removes only recognized
`v95-<UTC timestamp>-<hex>.bse` archives older than the configured
age; unrelated files and symlinks are untouched. No plaintext JSON
intermediate file is written. This is not evidence of production
backup readiness.

## 3. Alembic versioned schema pilot

`alembic_v95.ini` + `migrations_v95/` include:

- `0001_v95`: baseline creates the **empty synthetic** BeerShop schema only
  if the database contains no application tables
- `0002_v95`: explicit additive rate-counter table migration

Example for **private temp SQLite fixture**:

```sh
env -u DATABASE_URL -u BEERSHOP_PERSISTENT_DATABASE_URL \
  BEERSHOP_V95_MIGRATION_ACK=ISOLATED_FIXTURE_ONLY \
  BEERSHOP_V95_MIGRATION_URL=sqlite:////tmp/private-test/beershop-v95-migration-demo.db \
  python -m alembic -c alembic_v95.ini upgrade head
```

The path's parent must exist, be private, and be inside OS temp. A separate
PostgreSQL option accepts only localhost and **exactly**
`beershop_v95_migration`, with
`BEERSHOP_V95_ALLOW_LOCAL_PG_MIGRATION=1`. CI creates a separate
disposable database to exercise this option. Remote connections, live
credentials, wrong database names, missing acknowledgement, existing
application tables and downgrades are rejected.

**Limit:** The first revision creates schema from current Python model
metadata, not fully frozen historical `op.create_table` operations.
Consequently this is a **versioned migration pilot, not a production-safe
historical migration chain**. Production startup still uses
`Base.metadata.create_all()`, and no automatic migration against a
real database is authorized.

## P0 work still required for real sales

- Production-safe, fully frozen Alembic revisions and verified reversible
  schema/data migrations with rehearsed rollback
- Independent encrypted offsite PostgreSQL/Neon backups with tested key
  custody, monitoring, disaster recovery and controlled rotation
- Trusted-proxy/edge rate limits, anti-DoS load test and counter TTL cleanup
- Payment confirmation, stock reconciliation, fiscal/legal/return/privacy
  readiness for Ukraine, MFA break-glass policy
- Operator approval of a separate rollout plan **after** all reviews

**Nothing in this PR automatically enables the new security flags.**
