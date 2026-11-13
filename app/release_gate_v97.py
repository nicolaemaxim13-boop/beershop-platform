"""BeerShop v9.7 conservative source-only release readiness gate.

Never contacts databases, payment providers, Render, Neon or remote services.
It checks for the existence of specific staging safeguards and identifies
release blockers. Source checks are NOT proof of a secure deployment.
No account values, credentials, buyer data or configuration secrets are read.
"""
from datetime import datetime, timezone
from pathlib import Path

STAGING_CONTROLS = (
    'isolated_mfa_rbac',
    'database_api_throttle',
    'signed_edge_identity',
    'encrypted_fixture_backup',
    'authenticated_fixture_restore',
    'guarded_fixture_migrations',
    'readonly_migration_preflight',
    'owner_mfa_encrypted_http_export_pilot',
    'premigrated_schema_startup_pilot',
)

# Independent human/infrastructure proof is not available in the repository.
# Never promote these blockers based on environment variables or a green CI.
MANUAL_P0 = (
    ('real_backup_key_custody', 'data',
     'Encrypted offsite production backups, secret escrow and rotation not independently verified'),
    ('independent_neon_restore', 'data',
     'Production-like restore on an independently isolated Neon test project not verified'),
    ('live_proxy_waf', 'security',
     'Signed edge service, network isolation, DDoS protection and ingress policy not deployed/reviewed'),
    ('security_external_review', 'security',
     'Independent penetration test, monitoring and incident response not approved'),
    ('merchant_ukraine_compliance', 'legal',
     'Ukrainian seller identity, fiscal receipts, refunds, privacy and offer terms require professional review'),
    ('payments_reconciliation', 'finance',
     'Live bank ownership, payments, refunds and manual settlement controls not independently verified'),
    ('physical_inventory_audit', 'inventory',
     'Supplier commitments and physical stock were not reconciled against actual goods'),
    ('production_change_approval', 'governance',
     'Owner sign-off, staged rollout, rollback plan and production deployment evidence are absent'),
)

FILES = {
    'app/main.py',
    'app/staff_v9.py',
    'app/rbac_v91.py',
    'app/api_rate_v95.py',
    'app/trusted_proxy_v96.py',
    'app/auto_backup_v95.py',
    'app/pg_encrypted_restore_v96.py',
    'app/migration_preflight_v96.py',
    'app/encrypted_export_v98.py',
    'app/startup_preflight_v98.py',
    'migrations_v95/env.py',
    'migrations_v95/versions/0001_isolated_baseline.py',
    'migrations_v95/versions/0002_rate_counters.py',
    '.github/workflows/ci.yml',
}


def audit_repository(root):
    root = Path(root)
    # Only fixed repository-relative file paths, never user-provided filenames.
    source = {}
    for name in sorted(FILES):
        try:
            source[name] = (root / name).read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            source[name] = ''

    def contains(name, *needles):
        return all(needle in source[name] for needle in needles)

    controls = {
        'isolated_mfa_rbac': (
            contains('app/staff_v9.py', 'def owner_step_up', 'StaffMfaRecovery')
            and contains('app/rbac_v91.py', 'require_isolated_staging',
                         'verify_staging_database')),
        'database_api_throttle': contains(
            'app/api_rate_v95.py', 'validate_staging',
            'v95_api_rate_counters', 'status_code=429'),
        'signed_edge_identity': contains(
            'app/trusted_proxy_v96.py', 'hmac.compare_digest',
            'BEERSHOP_V96_TRUSTED_EDGE_PEERS', 'getlist(name)'),
        'encrypted_fixture_backup': contains(
            'app/auto_backup_v95.py', 'encrypt_archive(raw, passphrase)',
            'write_private_exclusive', 'SYNTHETIC_LOCAL_SQLITE_ONLY'),
        'authenticated_fixture_restore': contains(
            'app/pg_encrypted_restore_v96.py',
            'decrypt_archive(envelope, passphrase)',
            'rehearsal_restore_empty_postgres'),
        'guarded_fixture_migrations': (
            contains('migrations_v95/env.py', 'guarded_local_url',
                     'ISOLATED_FIXTURE_ONLY')
            and contains('migrations_v95/versions/0002_rate_counters.py',
                         'down_revision = "0001_v95"')),
        'readonly_migration_preflight': contains(
            'app/migration_preflight_v96.py', 'inspect_isolated_existing',
            'PRAGMA query_only=ON', 'SET TRANSACTION READ ONLY'),
        'owner_mfa_encrypted_http_export_pilot': contains(
            'app/encrypted_export_v98.py', 'encrypted_snapshot_from_session',
            'require_synthetic_encrypted_export', 'encrypt_archive(raw, passphrase)'),
        'premigrated_schema_startup_pilot': contains(
            'app/startup_preflight_v98.py', 'verify_premigrated_synthetic_schema',
            'inspect_existing', 'EXPECTED_REVISION'),
    }
    staging = [
        {'id': key, 'status': 'observed_in_source' if controls[key] else 'missing_from_source',
         'note': 'Static presence check only; infrastructure and security are not certified.'}
        for key in STAGING_CONTROLS
    ]
    # These unresolved conditions are deliberately independent from CI success.
    production = [
        {'id': key, 'area': area, 'status': 'block', 'reason': why}
        for key, area, why in MANUAL_P0
    ]
    model_bootstrap = 'Base.metadata.create_all(engine)' in source['app/main.py']
    metadata_driven_migration = (
        'Base.metadata.create_all(bind' in
        source['migrations_v95/versions/0001_isolated_baseline.py']
    )
    plaintext_export = (
        'def maintenance_export' in source['app/main.py']
        and "json.dumps(snapshot" in source['app/main.py']
    )
    production.extend((
        {
            'id': 'runtime_auto_schema_bootstrap', 'area': 'migration',
            'status': 'block',
            'reason': ('Startup still creates tables from current models'
                       if model_bootstrap else
                       'Removal of startup schema bootstrap is not independently verified'),
        },
        {
            'id': 'historical_migrations_not_frozen', 'area': 'migration',
            'status': 'block',
            'reason': ('Alembic initial revision imports current model metadata'
                       if metadata_driven_migration else
                       'No independently approved historical migration chain'),
        },
        {
            'id': 'plaintext_maintenance_export', 'area': 'privacy',
            'status': 'block',
            'reason': ('Server maintenance export emits plaintext JSON with potential buyer data'
                       if plaintext_export else
                       'Encrypted server export and access controls not independently verified'),
        },
    ))
    return {
        'version': 'BeerShop v9.7',
        'mode': 'READ_ONLY_SOURCE_AUDIT',
        'decision': 'NO_GO',
        'production_ready': False,
        'independent_security_certification': False,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'source_files_examined': len(source),
        'staging_controls': staging,
        'staging_controls_observed': sum(controls.values()),
        'staging_controls_required': len(STAGING_CONTROLS),
        'production_blockers': production,
        'blocker_count': len(production),
        'manual_approval_required': True,
        'inspected_secrets': False,
        'database_queries': 0,
        'external_requests': 0,
        'writes_to_business_data': 0,
        'note': ('Green CI proves test results, not readiness to accept payments. '
                 'Production validation requires independent human and infrastructure evidence.'),
    }


def assert_staging_controls_present(result):
    """CI regression guard, not an approval to merge or deploy."""
    observed = {item['id'] for item in result['staging_controls']
                if item['status'] == 'observed_in_source'}
    return observed == set(STAGING_CONTROLS)
