/* BeerShop v5: discover, compare and buy. Checkout/FX stay server-authoritative. */
(function () {
  'use strict';
  const selected = new Map();
  const categories = [];
  let detailProduct = null;
  let debounce = null;

  // Imported product image URLs sometimes return 403/404. The browser must
  // never render their lengthy alt strings at the old oversized art font.
  function imageFailed(img) {
    const parent = img.parentElement;
    if (!parent || !parent.matches('.art, .product-detail-art, .compare-photo')) return;
    img.hidden = true;
    if (parent.querySelector('.image-error-fallback')) return;
    const placeholder = document.createElement('span');
    placeholder.className = 'image-error-fallback';
    placeholder.setAttribute('role', 'img');
    placeholder.setAttribute('aria-label', 'Фото товару недоступне');
    const symbol = document.createElement('span');
    symbol.textContent = '📦';
    symbol.setAttribute('aria-hidden', 'true');
    const caption = document.createElement('small');
    caption.textContent = 'Фото недоступне';
    placeholder.append(symbol, caption);
    parent.appendChild(placeholder);
  }
  document.addEventListener('error', event => {
    if (event.target instanceof HTMLImageElement) imageFailed(event.target);
  }, true);
  function checkFailedImages(root) {
    root.querySelectorAll('.art img, .product-detail-art img, .compare-photo img')
      .forEach(img => { if (img.complete && img.naturalWidth === 0) imageFailed(img); });
  }

  function imageUrl(product) {
    const src = typeof product.image_url === 'string' ? product.image_url.trim() : '';
    return (/^https:\/\//i.test(src) || src.startsWith('/assets/') || src.startsWith('/uploads/')) ? src : '';
  }

  function imageMarkup(product) {
    const src = imageUrl(product);
    return src
      ? '<img src="' + safe(src) + '" alt="' + safe(product.name) + '" loading="lazy" decoding="async">'
      : '<span class="product-image-fallback" role="img" aria-label="Фотографія ще не додана">⚙️</span>';
  }

  function galleryUrls(product) {
    const raw = Array.isArray(product.images) && product.images.length
      ? product.images : [product.image_url];
    return [...new Set(raw.filter(url => typeof url === 'string' &&
        (/^https:\/\//i.test(url) || url.startsWith('/assets/') ||
         /^\/uploads\/[0-9a-f]{32}\.(jpg|png|webp)$/.test(url))))].slice(0, 6);
  }
  function galleryMarkup(product) {
    const photos = galleryUrls(product);
    const thumbnails = photos.length > 1
      ? '<div class="product-gallery-thumbs" role="group" aria-label="Фотографії товару">' +
        photos.map((url, index) => '<button class="product-gallery-thumb" type="button" data-photo-index="' +
          index + '" aria-label="Переглянути фото ' + (index + 1) +
          '" aria-pressed="' + (index === 0 ? 'true' : 'false') + '">' +
          '<img src="' + safe(url) + '" alt="" loading="lazy" decoding="async"></button>').join('') + '</div>'
      : '';
    return '<div class="product-gallery">' +
      '<div class="product-detail-art" id="detail-main-photo">' + imageMarkup(product) + '</div>' +
      thumbnails + (photos.length > 1 ? '<p class="product-gallery-count" id="detail-photo-count">Фото 1 із ' +
        photos.length + '</p>' : '') + '</div>';
  }
  function switchGalleryPhoto(product, index) {
    const photos = galleryUrls(product);
    if (index < 0 || index >= photos.length) return;
    const art = document.getElementById('detail-main-photo');
    if (!art) return;
    art.innerHTML = '<img src="' + safe(photos[index]) + '" alt="' +
      safe(product.name) + ', фото ' + (index + 1) + '" loading="eager" decoding="async">';
    document.querySelectorAll('.product-gallery-thumb').forEach(button =>
      button.setAttribute('aria-pressed', String(Number(button.dataset.photoIndex) === index)));
    const counter = document.getElementById('detail-photo-count');
    if (counter) counter.textContent = 'Фото ' + (index + 1) + ' із ' + photos.length;
    checkFailedImages(art);
  }

  function primaryVariant(product) {
    return (product.variants || []).find(v => v.active && v.stock > 0) ||
           (product.variants || []).find(v => v.active) || null;
  }

  function stockText(variant) {
    return !variant ? 'Немає доступних варіантів' :
      Number(variant.stock) > 0 ? 'У наявності · ' + variant.stock + ' шт.' : 'Тимчасово немає в наявності';
  }

  function updateCompareTray() {
    const tray = document.getElementById('compare-tray');
    if (!tray) return;
    tray.classList.toggle('hidden', selected.size === 0);
    document.body.classList.toggle('has-compare', selected.size > 0);
    document.getElementById('compare-count').textContent = String(selected.size);
    document.getElementById('compare-go').disabled = selected.size < 2;
    document.getElementById('compare-hint').textContent = selected.size < 2
      ? 'Виберіть ще один товар для порівняння' : 'Порівняйте ціни, залишки та характеристики';
    document.querySelectorAll('[data-compare-product]').forEach(button => {
      const active = selected.has(Number(button.dataset.compareProduct));
      button.setAttribute('aria-pressed', String(active));
      button.textContent = active ? '✓ У порівнянні' : 'Порівняти';
    });
  }

  function toggleCompare(product) {
    if (selected.has(product.id)) selected.delete(product.id);
    else if (selected.size >= 3) { msg('Можна порівняти не більше 3 товарів'); return; }
    else selected.set(product.id, product);
    updateCompareTray();
  }

  function syncCardStock(card, product) {
    const selectedId = Number(card.querySelector('.variants')?.value || 0);
    const variant = (product.variants || []).find(v => v.id === selectedId) || primaryVariant(product);
    const availability = card.querySelector('.product-stock-badge');
    const available = Boolean(variant && variant.active && Number(variant.stock) > 0);
    if (availability) {
      availability.textContent = available ? '● У наявності · ' + variant.stock + ' шт.' : '○ Немає в наявності';
      availability.classList.toggle('is-out', !available);
    }
    const addButton = card.querySelector('.row button');
    if (addButton) addButton.disabled = !available;
  }

  function decorateCatalog() {
    const cards = [...document.querySelectorAll('#cards .card')];
    cards.forEach((card, i) => {
      const product = products[i];
      if (!product) return;
      const media = card.querySelector('.art');
      const stockBadge = document.createElement('span');
      stockBadge.className = 'product-stock-badge';
      media?.appendChild(stockBadge);
      const heading = card.querySelector('h3');
      if (heading) {
        const titleLink = document.createElement('a');
        titleLink.href = '/products/' + encodeURIComponent(product.slug);
        titleLink.textContent = heading.textContent;
        heading.replaceChildren(titleLink);
      }
      const description = card.querySelector('p.muted');
      if (description) description.title = product.description || '';
      syncCardStock(card, product);
      const existing = card.querySelector('.product-actions');
      if (existing) existing.remove();
      const actions = document.createElement('div');
      actions.className = 'product-actions';
      const link = document.createElement('a');
      link.className = 'product-link';
      link.href = '/products/' + encodeURIComponent(product.slug);
      link.textContent = 'Деталі товару';
      link.setAttribute('aria-label', 'Деталі: ' + product.name);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'compare-toggle';
      button.dataset.compareProduct = String(product.id);
      button.onclick = () => toggleCompare(product);
      actions.append(link, button);
      card.append(actions);
    });
    checkFailedImages(document.getElementById('cards'));
    updateCompareTray();
  }

  const legacyRenderProducts = renderProducts;
  renderProducts = function () {
    legacyRenderProducts();
    decorateCatalog();
  };

  function setCategory(slug) {
    document.getElementById('category').value = slug;
    syncCategoryChips();
    reload();
  }

  function syncCategoryChips() {
    const selectedSlug = document.getElementById('category').value;
    document.querySelectorAll('#quick-categories button').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.category === selectedSlug));
    });
  }

  async function loadQuickCategories() {
    try {
      const response = await fetch('/api/categories');
      if (!response.ok) return;
      const rows = await response.json();
      categories.splice(0, categories.length, ...rows);
      const nav = document.getElementById('quick-categories');
      nav.replaceChildren();
      [{slug: '', name: 'Усі товари'}, ...categories].forEach(category => {
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.category = category.slug;
        button.className = 'category-pill';
        button.textContent = category.name;
        button.addEventListener('click', () => setCategory(category.slug));
        nav.append(button);
      });
      syncCategoryChips();
    } catch (_) {
      // The original select remains usable without the shortcut controls.
    }
  }

  window.queueReload = function () {
    clearTimeout(debounce);
    debounce = setTimeout(reload, 260);
  };
  window.clearProductFilters = function () {
    const search = document.getElementById('search');
    const category = document.getElementById('category');
    const stock = document.getElementById('stock-only');
    search.value = '';
    category.value = '';
    stock.checked = false;
    syncCategoryChips();
    reload();
  };

  window.openCompare = function () {
    if (selected.size < 2) return;
    const items = [...selected.values()];
    const attributeNames = [...new Set(items.flatMap(p =>
      Object.keys((primaryVariant(p) || {}).attributes || {})))].slice(0, 15);
    const tableRows = [
      ['Ціна від', p => {
        const v = primaryVariant(p); return v ? fmt(Number(v.price)) : '—';
      }],
      ['Наявність', p => stockText(primaryVariant(p))],
      ['Артикул', p => (primaryVariant(p) || {}).sku || '—'],
      ...attributeNames.map(name => [name, p => ((primaryVariant(p) || {}).attributes || {})[name] || '—'])
    ];
    let markup = '<div class="compare-grid" style="--compare-cols:' + items.length + '">';
    markup += '<div class="compare-row compare-products"><strong class="compare-label">Товар</strong>';
    markup += items.map(p =>
      '<div class="compare-product"><div class="compare-photo">' + imageMarkup(p) +
      '</div><a href="/products/' + encodeURIComponent(p.slug) + '">' + safe(p.name) + '</a></div>').join('');
    markup += '</div>';
    for (const [label, value] of tableRows) {
      markup += '<div class="compare-row"><strong class="compare-label">' + safe(label) + '</strong>';
      markup += items.map(p => '<div>' + safe(String(value(p))) + '</div>').join('');
      markup += '</div>';
    }
    markup += '</div>';
    document.getElementById('compare-content').innerHTML = markup;
    checkFailedImages(document.getElementById('compare-content'));
    document.getElementById('compare-dialog').showModal();
  };
  window.closeCompare = function () {
    document.getElementById('compare-dialog').close();
  };
  window.clearCompare = function () {
    selected.clear();
    updateCompareTray();
    const dialog = document.getElementById('compare-dialog');
    if (dialog.open) dialog.close();
  };

  function updateDetailChoice() {
    if (!detailProduct) return;
    const variantId = Number(document.getElementById('detail-variant').value);
    const variant = detailProduct.variants.find(v => v.id === variantId);
    document.getElementById('detail-price').textContent = variant ? fmt(Number(variant.price)) : 'Ціна за запитом';
    document.getElementById('detail-stock').textContent = stockText(variant);
    document.getElementById('detail-add').disabled = !variant || variant.stock < 1;
    document.getElementById('detail-attributes').innerHTML = variant
      ? Object.entries(variant.attributes || {}).map(([key, value]) =>
          '<div><dt>' + safe(key) + '</dt><dd>' + safe(value) + '</dd></div>').join('')
      : '';
  }

  window.addDetailItem = function () {
    if (!detailProduct) return;
    const variant = detailProduct.variants.find(v => v.id === Number(document.getElementById('detail-variant').value));
    if (!variant || variant.stock < 1) { msg('Немає товару в наявності'); return; }
    const qty = (cart[variant.id]?.qty || 0) + 1;
    if (qty > variant.stock) { msg('Недостатньо товару на складі'); return; }
    cart[variant.id] = {qty, name:detailProduct.name, sku:variant.sku, price:Number(variant.price), stock:variant.stock};
    document.getElementById('checkout-confirmation').classList.add('hidden');
    renderCart();
    toggleCart(true);
  };

  function showDetail(product) {
    detailProduct = product;
    const variants = (product.variants || []).filter(v => v.active);
    const section = document.getElementById('product-view');
    document.querySelector('.hero').classList.add('hidden');
    const category = categories.find(c => c.id === product.category_id);
    section.innerHTML =
      '<a class="back-link" href="/#catalog">← Назад до каталогу</a>' +
      '<div class="product-detail-grid">' + galleryMarkup(product) +
      '<div class="product-detail-info"><p class="tag">BeerShop / ' + safe(category?.name || 'Обладнання') + '</p>' +
      '<h1>' + safe(product.name) + '</h1><p class="product-description">' + safe(product.description || 'Професійне обладнання для розливу пива.') + '</p>' +
      '<label for="detail-variant">Варіант / артикул</label><select id="detail-variant" onchange="updateDetailChoice()">' +
      variants.map(v => '<option value="' + v.id + '">' + safe(v.sku) + ' · ' + safe(stockText(v)) + '</option>').join('') +
      '</select><div class="detail-buy"><strong id="detail-price"></strong><span id="detail-stock" class="muted"></span></div>' +
      '<button id="detail-add" type="button" onclick="addDetailItem()">+ Додати у кошик</button>' +
      '<button id="detail-compare" type="button" class="detail-compare" data-compare-product="' + product.id + '">Порівняти</button>' +
      '<dl id="detail-attributes" class="product-specs"></dl>' +
      '<p class="muted">Ціна в гривнях уточнюється при оформленні замовлення. Оплата — тільки після підтвердження менеджером.</p>' +
      '</div></div>';
    section.classList.remove('hidden');
    checkFailedImages(section);
    section.querySelectorAll('[data-photo-index]').forEach(button =>
      button.addEventListener('click', () => switchGalleryPhoto(product, Number(button.dataset.photoIndex))));
    document.getElementById('detail-variant').addEventListener('change', updateDetailChoice);
    document.getElementById('detail-compare').addEventListener('click', () => toggleCompare(product));
    document.title = product.name + ' — BeerShop';
    updateDetailChoice();
    updateCompareTray();
  }
  window.updateDetailChoice = updateDetailChoice;
  const legacyUpdatePrice = updatePrice;
  updatePrice = function (productId) {
    legacyUpdatePrice(productId);
    const card = document.querySelector('#cards .card #variant-' + productId)?.closest('.card');
    const product = products.find(p => p.id === productId);
    if (card && product) syncCardStock(card, product);
  };

  async function loadProductPage() {
    if (!location.pathname.startsWith('/products/')) return;
    const section = document.getElementById('product-view');
    section.classList.remove('hidden');
    document.querySelector('.hero').classList.add('hidden');
    section.textContent = 'Завантаження товару…';
    const slug = decodeURIComponent(location.pathname.substring('/products/'.length));
    try {
      const response = await fetch('/api/products/' + encodeURIComponent(slug));
      if (!response.ok) throw Error(response.status === 404 ? 'Товар не знайдено' : 'Не вдалося завантажити товар');
      showDetail(await response.json());
    } catch (error) {
      section.innerHTML = '<div class="empty"><h1>' + safe(error.message) +
        '</h1><p>Перевірте посилання або поверніться до каталогу.</p><a href="/#catalog" class="cta">Переглянути каталог</a></div>';
    }
  }

  const originalAdd = add;
  add = function (id) {
    document.getElementById('checkout-confirmation').classList.add('hidden');
    originalAdd(id);
  };
  const originalRenderCart = renderCart;
  renderCart = function () {
    originalRenderCart();
    const count = Object.values(cart).reduce((n, item) => n + item.qty, 0);
    document.getElementById('checkout-count').textContent = String(count);
  };

  // Invoke the current catalog loader (v7 replaces it with race-safe pagination).
  document.getElementById('stock-only').addEventListener('change', () => reload());
  document.getElementById('category').addEventListener('change', syncCategoryChips);
  if (document.querySelector('#cards .card')) decorateCatalog();
  loadQuickCategories().then(loadProductPage);
})();
