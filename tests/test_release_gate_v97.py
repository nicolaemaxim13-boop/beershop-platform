"""v9.7 conservative release gate: NO_GO is never an automatic certification."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from fastapi.testclient import TestClient

import app.main as main
from app.release_gate_v97 import (
    FILES, STAGING_CONTROLS, MANUAL_P0,
    audit_repository, assert_staging_controls_present)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_audit_v97.py"
ADMIN = {'Authorization': 'Bearer ' + 't' * 40}
AUDIT = {'Authorization': 'Bearer ' + 'a' * 40}


def test_v97_no_go_all_blockers_are_independent_of_flags(monkeypatch):
    monkeypatch.setenv('BEERSHOP_V95_RATE_LIMIT', '1')
    monkeypatch.setenv('BEERSHOP_V96_EDGE_SIGNED_CLIENT', '1')
    monkeypatch.setenv('BEERSHOP_V95_RATE_KEY', 'DO-NOT-ECHO-PRIVATE-V95-KEY')
    monkeypatch.setenv('BEERSHOP_V96_EDGE_KEY', 'DO-NOT-ECHO-PRIVATE-V96-KEY')
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 'DO-NOT-ECHO-ADMIN-TOKEN')
    report = audit_repository(ROOT)
    assert report['mode'] == 'READ_ONLY_SOURCE_AUDIT'
    assert report['decision'] == 'NO_GO'
    assert report['production_ready'] is False
    assert not report['independent_security_certification']
    assert report['manual_approval_required']
    assert report['database_queries'] == report['external_requests'] == 0
    assert report['writes_to_business_data'] == 0
    assert report['inspected_secrets'] is False
    assert assert_staging_controls_present(report), report['staging_controls']
    assert report['staging_controls_observed'] == len(STAGING_CONTROLS)
    reasons = {item['id']: item for item in report['production_blockers']}
    assert {x[0] for x in MANUAL_P0} <= set(reasons)
    assert all(x['status'] == 'block' for x in reasons.values())
    for key in ('runtime_auto_schema_bootstrap', 'historical_migrations_not_frozen',
                'plaintext_maintenance_export', 'physical_inventory_audit',
                'payments_reconciliation', 'merchant_ukraine_compliance'):
        assert key in reasons
    assert report['blocker_count'] >= 11
    serialized = json.dumps(report, ensure_ascii=False)
    for value in ('DO-NOT-ECHO-PRIVATE-V95-KEY', 'DO-NOT-ECHO-PRIVATE-V96-KEY',
                  'DO-NOT-ECHO-ADMIN-TOKEN', 'postgresql://'):
        assert value not in serialized
    assert all('password' not in x for x in report['staging_controls'])


def test_v97_missing_guard_fails_ci_regression_not_ready(tmp_path):
    for name in FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    baseline = audit_repository(tmp_path)
    assert assert_staging_controls_present(baseline)
    edge = tmp_path / 'app/trusted_proxy_v96.py'
    edge.write_text(edge.read_text().replace('hmac.compare_digest', 'unsafe_comparison'),
                    encoding='utf-8')
    altered = audit_repository(tmp_path)
    assert not assert_staging_controls_present(altered)
    control = {x['id']: x['status'] for x in altered['staging_controls']}
    assert control['signed_edge_identity'] == 'missing_from_source'
    assert altered['decision'] == 'NO_GO' and not altered['production_ready']
    # Missing source files must also fail closed, not throw a secret-bearing error.
    edge.unlink()
    missing = audit_repository(tmp_path)
    assert not assert_staging_controls_present(missing)


def test_v97_cli_creates_private_audit_without_overwrite(tmp_path):
    out = tmp_path / 'v97-readiness.json'
    args = [sys.executable, str(SCRIPT), '--assert-no-go',
            '--assert-staging-controls', '--output', str(out)]
    env = dict(os.environ)
    env['DATABASE_URL'] = 'postgresql://DELIBERATE-FAKE-SECRET@bad.invalid/live'
    env['BEERSHOP_PERSISTENT_DATABASE_URL'] = 'postgresql://fake@other.invalid/live'
    p = subprocess.run(args, env=env, cwd=ROOT, capture_output=True,
                       text=True, timeout=30, check=False)
    assert p.returncode == 0, p.stderr
    assert out.is_file()
    assert out.stat().st_mode & 0o777 == 0o600
    obj = json.loads(out.read_text(encoding='utf-8'))
    assert obj['decision'] == 'NO_GO' and not obj['production_ready']
    assert 'DELIBERATE-FAKE-SECRET' not in out.read_text()
    assert 'bad.invalid' not in p.stdout+p.stderr
    again = subprocess.run(args, env=env, cwd=ROOT, capture_output=True,
                           text=True, timeout=30, check=False)
    assert again.returncode == 2
    assert json.loads(out.read_text()) == obj


def test_v97_protected_endpoint_is_readonly_and_redacted(monkeypatch):
    monkeypatch.delenv('BEERSHOP_V95_RATE_LIMIT', raising=False)
    monkeypatch.delenv('BEERSHOP_V96_EDGE_SIGNED_CLIENT', raising=False)
    monkeypatch.delenv('BEERSHOP_V91_ENFORCE_RBAC', raising=False)
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    monkeypatch.setenv('BEERSHOP_V9_AUDIT_TOKEN', 'a' * 40)
    with TestClient(main.app) as c:
        endpoint = '/api/admin/v97/release-gate'
        assert c.get(endpoint).status_code == 401
        with main.SessionLocal() as db:
            from sqlalchemy import func, select
            orders_before = db.scalar(select(func.count()).select_from(main.Order))
        for role_headers in (ADMIN, AUDIT):
            response = c.get(endpoint, headers=role_headers)
            assert response.status_code == 200, response.text
            assert response.headers['cache-control'] == 'private, no-store'
            body = response.json()
            assert body['decision'] == 'NO_GO'
            assert body['production_ready'] is False
            assert body['database_queries'] == 0
            assert body['external_requests'] == 0
            assert body['writes_to_business_data'] == 0
            assert 'ttttttttttttt' not in response.text
            assert 'aaaaaaaaaaaaa' not in response.text
        with main.SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(main.Order)) == orders_before
