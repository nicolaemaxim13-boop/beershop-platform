"""Start an isolated local fixture for CI; never connect to a deployed database."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='beershop-browser-') as folder, socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    env = dict(os.environ)
    env.pop('BEERSHOP_PERSISTENT_DATABASE_URL', None)
    env.update(DATABASE_URL='sqlite:///' + str(Path(folder) / 'test.db'),
               BEERSHOP_ADMIN_TOKEN='t' * 40, CRM_TEST_TOKEN='t' * 40,
               KEYCRM_SYNC_ENABLED='0', PRIVATBANK_READ_ENABLED='0',
               PYTHONPATH=str(root), CRM_TEST_URL=f'http://127.0.0.1:{port}')
    for key in ('KEYCRM_API_KEY', 'NOVA_POSHTA_API_KEY', 'PRIVATBANK_TOKEN'):
        env.pop(key, None)
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'crm_preview:app',
                               '--app-dir', 'tests', '--fd', str(listener.fileno()),
                               '--no-access-log'], cwd=root, env=env, pass_fds=(listener.fileno(),))
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError('Fixture server failed to start')
            try:
                with urllib.request.urlopen(env['CRM_TEST_URL'] + '/api/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Fixture readiness timed out')
        for script in ('crm_browser.cjs', 'crm_react_browser.cjs', 'responsive_browser.cjs', 'storefront_v5_browser.cjs', 'gallery_v8_browser.cjs', 'readiness_v9_browser.cjs'):
            subprocess.run(['node', str(root / 'tests' / script)], cwd=root, env=env, check=True, timeout=180)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
