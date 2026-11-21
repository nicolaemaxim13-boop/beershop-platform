"""BeerShop v10.0: NO_GO-only inspection of existing synthetic DB schema.

This file cannot perform migrations, stamp revision identifiers, or read rows
of customer/business tables. Connection wrapper allows only v9.6 preflight
fixture targets; the result is metadata-only and never enables a deployment.
"""
import sqlite3
from urllib.parse import quote

from sqlalchemy import create_engine, inspect, text

EXPECTED_REVISION = '0002_v95'


def _explicit_index_signature(value):
    columns = value.get('column_names')
    if (not isinstance(columns, (list, tuple)) or not columns
            or any(not isinstance(x, str) or not x for x in columns)
            or not isinstance(value.get('name'), str)):
        return None
    # Explicit unique constraints can appear as duplicate indexes on
    # PostgreSQL and are checked separately by the preflight constraint check.
    if value.get('duplicates_constraint'):
        return 'skip'
    # Expression indexes and partial predicates require manual review.
    if (value.get('expressions') or value.get('include_columns')
            or value.get('dialect_options')):
        return None
    return (value['name'], tuple(columns), bool(value.get('unique', False)))


def _existing_revision(connection, table_names):
    if 'alembic_version' not in table_names:
        return 'UNVERSIONED'
    try:
        versions = connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all()
        if len(versions) == 1 and versions[0] == EXPECTED_REVISION:
            return 'EXPECTED_STAGING_REVISION'
        return 'UNRECOGNIZED_OR_AMBIGUOUS'
    except Exception:
        # Do not reveal arbitrary SQL error text, URL or row data.
        return 'UNREADABLE_VERSION'


def plan_existing_schema_adoption(connection, metadata):
    """Produce a *read-only*, never-approve migration/DR planning report.

    Caller must restrict connection to a disposable synthetic test DB.
    Zero customer rows, secrets and connection strings are included.
    """
    from .migration_preflight_v96 import inspect_existing

    base = inspect_existing(connection, metadata)
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    expected_tables = {table.name for table in metadata.sorted_tables}
    missing_indexes = {}
    extra_indexes = {}
    changed_indexes = {}
    opaque_indexes = []
    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        expected = {
            i.name: (i.name, tuple(col.name for col in i.columns), bool(i.unique))
            for i in table.indexes
        }
        observed = {}
        for item in inspector.get_indexes(table.name):
            sig = _explicit_index_signature(item)
            if sig == 'skip':
                continue
            if sig is None or sig[0] in observed:
                opaque_indexes.append(table.name)
                continue
            observed[sig[0]] = sig
        only_expected = sorted(set(expected) - set(observed))
        only_observed = sorted(set(observed) - set(expected))
        changed = sorted(name for name in set(expected) & set(observed)
                         if expected[name] != observed[name])
        if only_expected:
            missing_indexes[table.name] = only_expected
        if only_observed:
            extra_indexes[table.name] = only_observed
        if changed:
            changed_indexes[table.name] = changed
    version_status = _existing_revision(connection, existing_tables)
    structural_drift = not base['existing_schema_matches_model']
    index_drift = bool(missing_indexes or extra_indexes or changed_indexes or opaque_indexes)
    eligible_for_review = (not structural_drift and not index_drift and
                           version_status in ('UNVERSIONED', 'EXPECTED_STAGING_REVISION')
                           and bool(expected_tables) and expected_tables <= existing_tables)
    blockers = []
    if structural_drift:
        blockers.append('tables_columns_types_or_constraints_drift')
    if index_drift:
        blockers.append('index_inventory_requires_manual_review')
    if version_status != 'EXPECTED_STAGING_REVISION':
        blockers.append('baseline_revision_not_verified')
    blockers.extend((
        'existing_data_and_referential_integrity_not_audited',
        'online_lock_downtime_and_rollback_unverified',
        'independent_disaster_recovery_restore_not_verified',
        'production_approval_and_operational_review_missing',
    ))
    return {
        'version': 'BeerShop v10.0',
        'scope': 'ISOLATED_EXISTING_SCHEMA_READ_ONLY',
        'read_only': True,
        'decision': 'NO_GO',
        'writes_to_database': 0,
        'sql_migrations_run': 0,
        'rows_exposed': 0,
        'connection_details_exposed': False,
        'expected_revision': EXPECTED_REVISION,
        'revision_status': version_status,
        'candidate_for_manual_review': eligible_for_review,
        'schema_matches_models': bool(base['existing_schema_matches_model']),
        'index_inventory_matches_models': not index_drift,
        'table_count_compared': base['tables_compared'],
        'schema_issues': base['issues'],
        'index_issues': {
            'missing_indexes': missing_indexes,
            'unexpected_indexes': extra_indexes,
            'changed_indexes': changed_indexes,
            'uninspectable_indexes': sorted(set(opaque_indexes)),
        },
        'blockers': blockers,
        'next_action': ('Independent reviewer must assess a versioned, backed-up '
                        'baseline adoption with row-level validation and rollback; '
                        'no automatic Alembic stamp or live migration is permitted.'),
    }


def inspect_isolated_existing_plan(url, metadata):
    """Open only a v9.6-approved disposable fixture using read-only SQL."""
    from .migration_preflight_v96 import validate_preflight_target
    from .storage import sqlalchemy_url

    backend, address = validate_preflight_target(url)
    if backend == 'sqlite':
        # mode=ro prevents writes even if caller accidentally changes later.
        def connect():
            uri = 'file:' + quote(address, safe='/') + '?mode=ro'
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            connection.execute('PRAGMA query_only=ON')
            return connection
        engine = create_engine('sqlite://', creator=connect)
    else:
        engine = create_engine(sqlalchemy_url(address), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if backend == 'postgresql':
                connection.execute(text('SET TRANSACTION READ ONLY'))
            return plan_existing_schema_adoption(connection, metadata)
    finally:
        engine.dispose()
