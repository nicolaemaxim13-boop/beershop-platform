"""BeerShop P1: safe, non-public synthetic SEO preview.

No host-header-derived canonical URL, inferred merchant assertions or Offers.
No buyer data. The actual published domain and indexability require approval.
"""
import html
import re


def _text(value, limit):
    # Visible product copy only. Never generate raw executable HTML from it.
    cleaned = re.sub(r'<[^>]*>', ' ', value or '')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:limit]


def product_preview_html(template, product):
    name = _text(getattr(product, 'name', ''), 100) or 'Обладнання'
    detail = _text(getattr(product, 'description', ''), 155)
    if not detail:
        detail = 'Професійне обладнання для розливу пива — каталог BeerShop.'
    title = html.escape(name + ' — BeerShop', quote=True)
    description = html.escape(detail, quote=True)
    tags = (
        '<title>' + title + '</title>'
        '<meta name="description" content="' + description + '">'
        '<meta property="og:type" content="product">'
        '<meta property="og:title" content="' + title + '">'
        '<meta property="og:description" content="' + description + '">'
        '<meta name="robots" content="noindex,nofollow">'
    )
    # Only a known static marker is replaced; never interpolate into scripts.
    original = '<title>BeerShop — обладнання для розливу пива</title>'
    if template.count(original) != 1:
        raise ValueError('Unexpected storefront template')
    return template.replace(original, tags, 1)
