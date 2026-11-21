"""Test the user-facing backup verifier with synthetic SQLite-only data."""
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import app.main as main

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'verify_backup_v9.py'
AUTH = {'Authorization': 'Bearer ' + 't' * 40}


def command(filename, *, rehearse=False):
    env = dict(os.environ)
    env.pop('BEERSHOP_PERSISTENT_DATABASE_URL', None)
    env['BEERSHOP_ADMIN_TOKEN'] = 't' * 40
    env['DATABASE_URL'] = 'sqlite:///:memory:'
    args = [sys.executable, str(SCRIPT), str(filename)]
    if rehearse:
        args.append('--rehearse')
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=45, check=False)


def test_private_archive_offline_integrity_and_restore(tmp_path, monkeypatch):
    monkeypatch.setenv('BEERSHOP_ADMIN_TOKEN', 't' * 40)
    with TestClient(main.app) as c:
        response = c.get('/api/admin/maintenance/export', headers=AUTH)
        assert response.status_code == 200
        raw = response.content
    path = tmp_path / 'confidential-backup.json'
    path.write_bytes(raw)
    for rehearse in (False, True):
        result = command(path, rehearse=rehearse)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data['verified'] is True
        assert data['local_restore_rehearsed'] is rehearse
        assert data['tables'] == len(main.Base.metadata.sorted_tables)
        assert data['rows'] >= 1
        assert data['sha256'] == json.loads(raw)['integrity']['sha256']
        assert str(path) not in result.stdout
        assert 'BEERSHOP_ADMIN_TOKEN' not in result.stdout
        assert 'confidential-backup' not in result.stdout

    archive = json.loads(raw)
    archive['tables']['stores'][0]['name'] = 'TAMPERED'
    path.write_text(json.dumps(archive), encoding='utf-8')
    failure = command(path, rehearse=True)
    assert failure.returncode == 1
    assert not failure.stdout
    assert 'TAMPERED' not in failure.stderr


def test_offline_verifier_rejects_missing_or_incomplete_backup(tmp_path):
    missing = command(tmp_path / 'missing.json')
    assert missing.returncode == 1
    assert not missing.stdout
    invalid = tmp_path / 'invalid.json'
    invalid.write_text('{"format":"beershop-snapshot-v1"}', encoding='utf-8')
    fail = command(invalid)
    assert fail.returncode == 1
    assert 'Traceback' not in fail.stderr
