from fastapi.testclient import TestClient
from app.main import app


def test_product_photos_entry_and_assets():
    with TestClient(app) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert '${productImage(p)}' in page.text
        assert '/assets/store-media.js' in page.text
        assert '/assets/store-media.css' in page.text
        assert page.text.index('/assets/store-media.js') < page.text.index('function renderProducts')
        for path, content_type in [('/assets/store-media.js', 'application/javascript'),
                                   ('/assets/store-media.css', 'text/css')]:
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers['content-type'].startswith(content_type)
            assert response.headers['x-content-type-options'] == 'nosniff'
