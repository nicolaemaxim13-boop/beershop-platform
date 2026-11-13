# BeerShop v9.6 — isolated security hardening (Draft PR #17)

**STAGING ONLY**. No production deployment, main merge, Neon access,
real customer data, secrets, inventory mutation, payment processing or paid
services. Every new feature is opt-in and disabled by default.

## A. Authenticated encrypted PostgreSQL restore rehearsal

The v9.4 encrypted `.bse` envelope is decrypted **in memory** using
AES-256-GCM authentication, then the v9 integrity manifest and schema are
verified, before any database writes are attempted.
`app.pg_encrypted_restore_v96.rehearsal_restore_encrypted_postgres()`
allows only the exact empty local PostgreSQL database
`beershop_v96_encrypted_restore` with loopback host, an explicit
`BEERSHOP_V94_LOCAL_PG_REHEARSAL=1` and
`BEERSHOP_V96_ENCRYPTED_RESTORE_ACK=ISOLATED_ENCRYPTED_RESTORE_ONLY`.
Both `DATABASE_URL` and `BEERSHOP_PERSISTENT_DATABASE_URL` must be
unset. CI creates a separate disposable database for it.

Tests include wrong passphrase, tampered ciphertext, wrong/remote target,
successful counts and checksum, data-preserving readback, and refusing a
second restore when tables already exist. **No plaintext archive is written
to disk**. This is not an operational production restore: encrypted
offsite backups, key custody and independent Neon recovery remain P0.

## B. Existing-schema migration preflight (read-only)

`scripts/migration_preflight_v96.py` provides a deliberately **read-only**
review for an existing *synthetic* fixture, not an Alembic upgrade. It
compares table names, column names and SQLAlchemy type affinities, nullability,
PK, FK and unique constraints. Returns no customer records, credentials or
connection URLs. Differences are explicitly listed for human review; a
matching schema **still requires manual baseline review**.

The script requires the exact environment acknowledgement
`BEERSHOP_V96_PREFLIGHT_ACK=ISOLATED_READONLY_PREFLIGHT`, plus
`BEERSHOP_V96_PREFLIGHT_URL`, which must be either:

- an existing private `/tmp/**/beershop-v96-preflight-*.db` SQLite fixture;
- or the exact local PostgreSQL database `beershop_v96_preflight` with
  `BEERSHOP_V96_LOCAL_PG_PREFLIGHT=1`.

Live DB env vars cause rejection. SQLite uses `mode=ro` +
`PRAGMA query_only=ON`, PostgreSQL uses a read-only transaction.
No `CREATE TABLE`, `UPDATE`, Alembic `stamp`, or production migration
is performed by this script. It intentionally does **not** certify indexes,
triggers, extensions, access policies, data compatibility, backward/forward
schema compatibility or migration rollback.

For synthetic local testing only:

```sh
env -u DATABASE_URL -u BEERSHOP_PERSISTENT_DATABASE_URL \
  BEERSHOP_V96_PREFLIGHT_ACK=ISOLATED_READONLY_PREFLIGHT \
  BEERSHOP_V96_PREFLIGHT_URL=sqlite:////tmp/private-fixture/beershop-v96-preflight-demo.db \
  python scripts/migration_preflight_v96.py
```

The CLI returns status 0 for a model match; 3 for drift; 2 for unsafe or
invalid targets. Schema matches **must not** be interpreted as deployment
approval or permission to stamp an existing database automatically.

## C. Signed trusted-proxy identity for rate limiting

In v9.5, when requests pass through a reverse proxy, the ASGI peer may be
the proxy rather than the customer. To avoid trusting user-supplied
`X-Forwarded-For`, v9.6 adds an optional **HMAC-SHA256 signed proxy
identity** for rate-limit buckets.

Staging configuration:

```text
BEERSHOP_V95_RATE_LIMIT=1
BEERSHOP_V91_STAGING_ACK=ISOLATED_STAGING_ONLY
BEERSHOP_V95_RATE_KEY=<private random secret, 32+ bytes>
BEERSHOP_V96_EDGE_SIGNED_CLIENT=1
BEERSHOP_V96_EDGE_KEY=<independent private random secret, 32+ bytes>
BEERSHOP_V96_TRUSTED_EDGE_PEERS=<exact known proxy IP(s), comma separated>
```

**Do not put real values in this document, git, chat or logs.** The proxy
(not the browser) must strip any incoming `X-BeerShop-Edge-*` headers,
then set:

- `X-BeerShop-Edge-IP`: canonical client IPv4/IPv6;
- `X-BeerShop-Edge-Time`: Unix seconds;
- `X-BeerShop-Edge-Signature`: lower-case hex HMAC-SHA256 over
  `beershop-v96:<client-ip>:<timestamp>`, with the edge key.

Server validates that the true ASGI peer is **exactly** a configured proxy
IP, timestamp skew ≤30s, signature matches, no duplicate headers and the
address is canonical. Otherwise it returns HTTP 403. The signing key is
independent from MFA, backup and rate-limit keys. An ordinary forwarded
header cannot select another client bucket. The backend's ASGI server must
be configured to preserve the real proxy peer instead of rewriting it
from `X-Forwarded-For`, or legitimate requests will be rejected.

This feature **is not enabled** by the PR and no edge/proxy service has
been configured, paid for or deployed. It is defense in depth and is not
a WAF: protections against DDoS, signed-header replay within 30 seconds,
key rotation, proper edge stripping and real ingress/network restrictions
still need dedicated validation.

## Unresolved production blockers

- Alembic 0001 still uses Python model metadata rather than frozen
  historical migration definitions. No production migration approval.
- Existing databases need real migration rehearsal, row-level integrity,
  schema data compatibility, rollback and monitored zero-downtime releases.
- Edge proxy deployment, IP provenance and WAF validation remain open.
- Real backup scheduling/offsite encrypted copies, key escrow/rotation,
  access approvals, dedicated Neon restore rehearsal and incident drills.
- Ukrainian customer-data privacy, payment reconciliation, legal and stock
  validations before any live sales.

CI uses only synthetic data in disposable SQLite and PostgreSQL. Keep PR
#17 Draft; do not merge, deploy or activate new feature flags on production.
