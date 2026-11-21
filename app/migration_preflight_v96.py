"""v9.6 read-only preflight for EXISTING isolated staging schemas.

Reports migration blockers without stamping, DDL, DML or any data output.
This does not authorize a migration against a real database.
"""
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .schema_audit_v94 import schema_inventory
from .storage import sqlalchemy_url


class UnsafeMigrationPreflight(ValueError):
    pass


def validate_preflight_target(url):
    if (os.getenv('BEERSHOP_V96_PREFLIGHT_ACK') != 'ISOLATED_READONLY_PREFLIGHT'
            or os.getenv('DATABASE_URL')
            or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL')):
        raise UnsafeMigrationPreflight('Only isolated offline staging preflight allowed')
    try:
        parsed = make_url(url)
    except (ArgumentError, TypeError, ValueError):
        raise UnsafeMigrationPreflight('Invalid preflight target') from None
    if parsed.get_backend_name() == 'sqlite':
        path = Path(parsed.database or '')
        tmp = Path(tempfile.gettempdir()).resolve()
        if (not path.is_absolute() or path.is_symlink()
                or not path.is_file() or path.suffix != '.db'
                or not path.name.startswith('beershop-v96-preflight-')
                or not path.resolve().is_relative_to(tmp)
                or not path.parent.resolve().is_relative_to(tmp)
                or path.parent.stat().st_mode & 0o077
                or path.stat().st_mode & 0o077
                or parsed.host):
            raise UnsafeMigrationPreflight('Only private existing synthetic SQLite fixture')
        return 'sqlite', str(path)
    if (parsed.get_backend_name() == 'postgresql'
            and parsed.host in ('localhost', '127.0.0.1', '::1')
            and parsed.database == 'beershop_v96_preflight'
            and os.getenv('BEERSHOP_V96_LOCAL_PG_PREFLIGHT') == '1'):
        return 'postgresql', url
    raise UnsafeMigrationPreflight('Remote and nonfixture databases forbidden')


def _keys(items):
    return sorted(repr(tuple(i)) for i in items)


def _type_signature(t):
    # SQLAlchemy's type affinity is dialect-independent for core types.
    affinity = getattr(t, '_type_affinity', type(t))
    name = getattr(affinity, '__name__', type(t).__name__)
    return name, getattr(t, 'length', None), getattr(t, 'precision', None), getattr(t, 'scale', None)


def inspect_existing(connection, metadata):
    """Read-only structural comparison against SQLAlchemy models; NO stamping."""
    base = schema_inventory(connection, metadata)
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    model = {table.name: table for table in metadata.sorted_tables}
    type_drift = {}
    nullability_drift = {}
    primary_key_drift = {}
    foreign_key_drift = {}
    unique_constraint_drift = {}
    for name in sorted(existing & set(model)):
        table = model[name]
        actual_cols = {c['name']: c for c in inspector.get_columns(name)}
        for col in table.columns:
            actual = actual_cols.get(col.name)
            if actual is None:
                continue
            expected_type = _type_signature(col.type)
            found_type = _type_signature(actual['type'])
            if expected_type != found_type:
                type_drift.setdefault(name, []).append(col.name)
            if (not col.primary_key and actual['nullable'] != col.nullable):
                nullability_drift.setdefault(name, []).append(col.name)
        pk = set(inspector.get_pk_constraint(name).get('constrained_columns') or [])
        expected_pk = {col.name for col in table.primary_key.columns}
        if pk != expected_pk:
            primary_key_drift[name] = True
        current_fks = _keys(
            (tuple(fk['constrained_columns']), fk['referred_table'],
             tuple(fk['referred_columns'])) for fk in inspector.get_foreign_keys(name))
        defined_fks = _keys(
            (tuple(fk.parent.name for fk in fk_constraint.elements),
             fk_constraint.referred_table.name,
             tuple(fk.column.name for fk in fk_constraint.elements))
            for fk_constraint in table.foreign_key_constraints)
        if current_fks != defined_fks:
            foreign_key_drift[name] = True
        from sqlalchemy import UniqueConstraint
        expected_unique = _keys(
            tuple(col.name for col in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint))
        found_unique = _keys(
            tuple(constraint['column_names'])
            for constraint in inspector.get_unique_constraints(name)
            if constraint.get('column_names'))
        if expected_unique != found_unique:
            unique_constraint_drift[name] = True
    issues = {
        'missing_tables': base['missing_tables'],
        'extra_tables': base['extra_tables'],
        'missing_columns': base['missing_columns'],
        'extra_columns': base['extra_columns'],
        'column_type_drift': type_drift,
        'nullability_drift': nullability_drift,
        'primary_key_drift': primary_key_drift,
        'foreign_key_drift': foreign_key_drift,
        'unique_constraint_drift': unique_constraint_drift,
    }
    ready = not any(bool(v) for v in issues.values())
    return {
        'read_only': True,
        'requires_manual_baseline_review': True,
        'existing_schema_matches_model': ready,
        'alembic_version_present': 'alembic_version' in existing,
        'tables_compared': len(existing & set(model)),
        'issues': issues,
        'warning': 'Comparison does not check business data, index plans, triggers, extensions, '
                   'security policy or compatibility with historical revisions. NEVER auto-stamp.',
    }


def inspect_isolated_existing(url, metadata):
    backend, address = validate_preflight_target(url)
    if backend == 'sqlite':
        # Read-only SQLite URI (mode=ro) prevents any unintended CREATE/UPDATE.
        def connect():
            uri = 'file:' + quote(address, safe='/') + '?mode=ro'
            connection = sqlite3.connect(uri, uri=True)
            connection.execute('PRAGMA query_only=ON')
            return connection
        engine = create_engine('sqlite://', creator=connect)
    else:
        engine = create_engine(sqlalchemy_url(address), pool_pre_ping=True)
    try:
        with engine.connect() as db:
            if backend == 'postgresql':
                db.execute(text('SET TRANSACTION READ ONLY'))
            return inspect_existing(db, metadata)
    finally:
        engine.dispose()
