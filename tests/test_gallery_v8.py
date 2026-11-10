"""v8 gallery: persistent ordered external photo URLs with atomic authenticated writes."""
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

AUTH = {'Authorization': 'Bearer ' + 't' * 40}


def test_gallery_urls_and_product_metadata_stay_consistent():
    with TestClient(app) as c:
        cat = c.get('/api/categories').json()[0]['id']
        slug = 'gallery-' + uuid4().hex[:12]
        created = c.post('/api/admin/products', headers=AUTH, json={
            'category_id': cat, 'slug': slug, 'name': 'Gallery fixture',
            'image_url': 'https://example.com/primary.jpg'
        })
        assert created.status_code == 201, created.text
        pid = created.json()['id']
        endpoint = f'/api/admin/products/{pid}/gallery'
        original = c.get('/api/products/' + slug).json()
        assert original['images'] == ['https://example.com/primary.jpg']
        assert c.put(endpoint, json={'images': []}).status_code == 401
        assert c.put('/api/admin/products/99999999/gallery',
                     headers=AUTH, json={'images': []}).status_code == 404

        photos = [f'https://cdn.example.com/photo-{i}.webp' for i in range(6)]
        saved = c.put(endpoint, headers=AUTH, json={'images': photos})
        assert saved.status_code == 200, saved.text
        assert saved.json()['image_url'] == photos[0]
        assert saved.json()['images'] == photos
        public = c.get('/api/products/' + slug).json()
        assert public['images'] == photos
        assert public['image_url'] == photos[0]
        assert next(x for x in c.get('/api/admin/products', headers=AUTH).json()
                    if x['id'] == pid)['images'] == photos
        ordered = [photos[2], photos[4], photos[0]]
        patched = c.patch(f'/api/admin/products/{pid}', headers=AUTH, json={
            'name': 'Reordered fixture', 'images': ordered
        })
        assert patched.status_code == 200, patched.text
        assert patched.json()['name'] == 'Reordered fixture'
        assert patched.json()['images'] == ordered
        assert patched.json()['image_url'] == photos[2]

        # Atomic validation: forbidden URLs do not change name or photo ordering.
        for invalid in (
            [*photos, 'https://cdn.example.com/extra.webp'],
            [photos[0], photos[0]],
            ['javascript:alert(1)'],
            ['http://images.example.com/photo.jpg'],
            ['data:image/svg+xml,%3Csvg%3E'],
            ['https://user:pass@cdn.example.com/photo.jpg'],
            ['https://cdn.example.com:8080/photo.jpg'],
            ['/uploads/../../secret'],
            ['https://cdn.example.com/' + 'z' * 510],
        ):
            fail = c.patch(f'/api/admin/products/{pid}', headers=AUTH,
                           json={'name': 'SHOULD NOT SAVE', 'images': invalid})
            assert fail.status_code == 422, (invalid, fail.status_code, fail.text)
            actual = c.get('/api/products/' + slug).json()
            assert actual['name'] == 'Reordered fixture'
            assert actual['images'] == ordered
        conflict = c.patch(f'/api/admin/products/{pid}', headers=AUTH, json={
            'image_url': photos[0], 'images': ordered
        })
        assert conflict.status_code == 422
        assert c.get('/api/products/' + slug).json()['images'] == ordered

        # Existing single-photo clients may update primary, keeping extras.
        legacy = c.patch(f'/api/admin/products/{pid}', headers=AUTH, json={
            'image_url': 'https://cdn.example.com/legacy-primary.png'
        })
        assert legacy.status_code == 200
        assert legacy.json()['images'] == [
            'https://cdn.example.com/legacy-primary.png', *ordered[1:]
        ]
        empty = c.put(endpoint, headers=AUTH, json={'images': []})
        assert empty.status_code == 200
        assert empty.json()['images'] == []
        assert empty.json()['image_url'] == ''


def test_gallery_assets_and_existing_admin_editor():
    with TestClient(app) as c:
        for endpoint, media in (
            ('/assets/admin-gallery-v8.js', 'javascript'),
            ('/assets/admin-gallery-v8.css', 'css'),
            ('/assets/storefront-gallery-v8.css', 'css'),
        ):
            r = c.get(endpoint)
            assert r.status_code == 200, r.text
            assert media in r.headers['content-type']
            assert r.headers['cache-control'] == 'no-store'
        admin = c.get('/manage')
        assert '/assets/admin-gallery-v8.js' in admin.text
        assert '/assets/admin-gallery-v8.css' in admin.text
        assert 'id="c32gallery"' in admin.text
        assert 'id="c32newimage"' in admin.text
        shop = c.get('/')
        assert '/assets/storefront-gallery-v8.css' in shop.text
