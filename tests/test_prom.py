from fastapi.testclient import TestClient
from app.main import app
from pathlib import Path
import os

def test_real_prom_file_preview_and_import():
    os.environ['BEERSHOP_ADMIN_TOKEN']='x'*40
    path=Path(__file__).resolve().parents[2] / 'export-products-24-05-23_21-34-44.csv'
    if not path.exists(): return
    with TestClient(app) as client:
        headers={'Authorization':'Bearer '+'x'*40}
        raw=path.read_bytes()
        result=client.post('/api/admin/prom/preview',headers=headers,files={'file':('catalog.csv',raw,'text/csv')})
        assert result.status_code==200,result.text
        assert result.json()['total']==100
        assert result.json()['currencies']=={'UAH':37,'USD':63}
        imported=client.post('/api/admin/prom/apply',headers=headers,files={'file':('catalog.csv',raw,'text/csv')})
        assert imported.status_code==200,imported.text
        assert imported.json()['added'] + imported.json()['updated'] == 100
        assert client.get('/api/products').json()['total'] > 0
