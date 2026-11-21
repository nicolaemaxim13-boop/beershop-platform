"""v9.8 explicit schema-managed startup rehearsal: no CREATE_ALL or backfill.

Only pre-created synthetic CI fixtures with exact Alembic version are
accepted. Never enable on real data, Neon, Render, or unknown local DBs.
"""
import os

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .migration_preflight_v96 import inspect_existing
from .rbac_v91 import require_isolated_staging

EXPECTED_REVISION = "0002_v95"
ACK = "PREMIGRATED_SYNTHETIC_DB_NO_AUTO_BOOTSTRAP"


def enabled():
    return os.getenv("BEERSHOP_V98_SCHEMA_MANAGED") == "1"


def verify_premigrated_synthetic_schema(*, engine, metadata,
                                       database_url, persistent_url):
    """Read-only boot guard. Must run before startup's schema/data writes."""
    if not enabled():
        raise HTTPException(503, "Schema-managed staging mode disabled")
    if (persistent_url
            or os.getenv("BEERSHOP_V98_SCHEMA_ACK") != ACK
            or os.getenv("BEERSHOP_V91_STAGING_ACK") != "ISOLATED_STAGING_ONLY"
            or os.getenv("BEERSHOP_V91_ENFORCE_RBAC") != "1"
            or os.getenv("BEERSHOP_V9_STAFF_AUTH") != "1"):
        raise HTTPException(503, "Isolated pre-migrated staging acknowledgement required")
    require_isolated_staging(persistent_url=persistent_url, database_url=database_url)
    try:
        url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError):
        raise HTTPException(503, "Invalid isolated schema URL") from None
    if (engine.url.get_backend_name() != url.get_backend_name()
            or engine.url.database != url.database or engine.url.host != url.host):
        raise HTTPException(503, "Schema connection does not match isolated fixture")
    backend = url.get_backend_name()
    if backend == "sqlite":
        from pathlib import Path
        if url.host or Path(url.database or "").name != "beershop_ci_test.db":
            raise HTTPException(503, "Only disposable CI SQLite fixture allowed")
    elif backend == "postgresql":
        if (url.host not in ("localhost", "127.0.0.1", "::1")
                or url.database != "beershop_test"
                or os.getenv("BEERSHOP_V91_ALLOW_LOCAL_POSTGRES") != "1"):
            raise HTTPException(503, "Only disposable CI PostgreSQL fixture allowed")
    else:
        raise HTTPException(503, "Unsupported pre-migrated test database")

    try:
        with engine.connect() as connection:
            if backend == "postgresql":
                connection.execute(text("SET TRANSACTION READ ONLY"))
            if backend == "sqlite":
                connection.execute(text("PRAGMA query_only=ON"))
            try:
                checked = inspect_existing(connection, metadata)
                if (not checked['existing_schema_matches_model']
                        or not checked['alembic_version_present']):
                    raise HTTPException(503, "Existing staging schema drift or unversioned database")
                rev = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                if rev != EXPECTED_REVISION:
                    raise HTTPException(503, "Unrecognized staging migration revision")
                return {"read_only": True, "schema_matches": True,
                        "alembic_revision": rev,
                        "auto_bootstrap_disabled": True}
            finally:
                if backend == "sqlite":
                    connection.execute(text("PRAGMA query_only=OFF"))
    except HTTPException:
        raise
    except Exception:
        # Don't return database exception strings; no credentials or row values.
        raise HTTPException(503, "Pre-migrated schema verification failed") from None
