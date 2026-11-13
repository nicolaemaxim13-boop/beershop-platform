"""Local-only Alembic migration environment: never touch Neon or Render."""
import os
import re
import tempfile
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def guarded_local_url():
    if (os.getenv("BEERSHOP_V95_MIGRATION_ACK") != "ISOLATED_FIXTURE_ONLY"
            or os.getenv("BEERSHOP_PERSISTENT_DATABASE_URL")
            or os.getenv("DATABASE_URL")):
        raise RuntimeError("Migration refused: live DB configured or fixture ACK absent")
    value = os.getenv("BEERSHOP_V95_MIGRATION_URL", "")
    if not value:
        raise RuntimeError("Migration URL missing")
    parsed = make_url(value)
    if parsed.get_backend_name() == "sqlite":
        path = Path(parsed.database or "")
        temp = Path(tempfile.gettempdir()).resolve()
        if (not path.is_absolute()
                or not path.name.startswith("beershop-v95-migration-")
                or path.suffix != ".db"
                or path.is_symlink()
                or not path.parent.is_dir()
                or not path.parent.resolve().is_relative_to(temp)
                or path.parent.stat().st_mode & 0o077
                or parsed.host):
            raise RuntimeError("Only a private /tmp synthetic SQLite fixture is allowed")
        return value
    if (parsed.get_backend_name() == "postgresql"
            and os.getenv("BEERSHOP_V95_ALLOW_LOCAL_PG_MIGRATION") == "1"
            and parsed.host in ("localhost", "127.0.0.1", "::1")
            and parsed.database == "beershop_v95_migration"):
        return value
    raise RuntimeError("Migration refused: remote or wrong database")


if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation not authorized for staging pilot")

# Validate BEFORE importing the application, which constructs its own engine.
url = guarded_local_url()
from app.main import Base  # noqa: E402
from app.storage import sqlalchemy_url  # noqa: E402

engine = create_engine(sqlalchemy_url(url))
try:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata,
                          compare_type=True, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
