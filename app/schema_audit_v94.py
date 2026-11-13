"""Read-only schema inventory for v9.4 migration planning (no DDL or DML).

This reports table/column differences only; it is NOT an Alembic migration
runner and must not be used as proof that a live migration is safe.
"""
from sqlalchemy import inspect


def schema_inventory(connection, metadata):
    inspector = inspect(connection)
    # Alembic bookkeeping is outside application metadata, not schema drift.
    actual = set(inspector.get_table_names()) - {'alembic_version'}
    expected = {table.name: table for table in metadata.sorted_tables}
    missing_tables = sorted(set(expected) - actual)
    extra_tables = sorted(actual - set(expected))
    missing_columns = {}
    extra_columns = {}
    for name in sorted(set(expected) & actual):
        wanted = {c.name for c in expected[name].columns}
        found = {c['name'] for c in inspector.get_columns(name)}
        if wanted - found:
            missing_columns[name] = sorted(wanted - found)
        if found - wanted:
            extra_columns[name] = sorted(found - wanted)
    return {
        'read_only': True,
        'schema_matches_columns': not (missing_tables or extra_tables
                                       or missing_columns or extra_columns),
        'expected_table_count': len(expected),
        'actual_table_count': len(actual),
        'missing_tables': missing_tables,
        'extra_tables': extra_tables,
        'missing_columns': missing_columns,
        'extra_columns': extra_columns,
        'note': 'Column names only. Types, constraints, indexes and data migrations require separate review.'
    }
