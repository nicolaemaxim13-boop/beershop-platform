/* BeerShop v7 — server-side retail-price sort, SKU search and linkable filters.
 * Overrides legacy pagination without touching checkout or inventory.
 */
(function () {
  'use strict';
  const VALID_SORT = new Set(['newest', 'price_asc', 'price_desc', 'name_asc', 'name_desc']);
  const LIMIT = 12;
  let revision = 0;
  let loadingMore = false;
  const byId = id => document.getElementById(id);

  function state() {
    return {
      q: byId('search').value.trim().slice(0, 100),
      category: byId('category').value,
      in_stock: byId('stock-only').checked,
      sort: VALID_SORT.has(byId('sort').value) ? byId('sort').value : 'newest'
    };
  }
  function updateShareableLink(filters) {
    if (location.pathname !== '/') return;
    const url = new URL(location.href);
    for (const key of ['q', 'category', 'in_stock', 'sort']) url.searchParams.delete(key);
    if (filters.q) url.searchParams.set('q', filters.q);
    if (filters.category) url.searchParams.set('category', filters.category);
    if (filters.in_stock) url.searchParams.set('in_stock', 'true');
    if (filters.sort !== 'newest') url.searchParams.set('sort', filters.sort);
    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
  }

  window.restoreCatalogFilters = function () {
    if (location.pathname !== '/') return;
    const args = new URLSearchParams(location.search);
    const q = args.get('q');
    if (q) byId('search').value = q.slice(0, 100);
    const slug = args.get('category') || '';
    if ([...byId('category').options].some(option => option.value === slug)) {
      byId('category').value = slug;
    }
    byId('stock-only').checked = args.get('in_stock') === 'true';
    const sort = args.get('sort') || 'newest';
    byId('sort').value = VALID_SORT.has(sort) ? sort : 'newest';
    const selected = byId('category').value;
    document.querySelectorAll('#quick-categories button').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.category === selected));
    });
  };

  function resultMessage() {
    const itemCount = products.length, filters = state();
    const flags = [];
    if (filters.q) flags.push('пошук: «' + filters.q + '»');
    if (filters.category) flags.push('за категорією');
    if (filters.in_stock) flags.push('лише в наявності');
    const prefix = total === 0 ? 'Товарів не знайдено' :
      'Показано ' + itemCount + ' із ' + total + ' товарів';
    byId('results-status').textContent = prefix + (flags.length ? ' · ' + flags.join(' · ') : '');
    byId('paging').textContent = itemCount && itemCount < total
      ? 'Ще ' + (total - itemCount) + ' товарів у каталозі' : '';
  }

  const renderBase = renderProducts;
  renderProducts = function () {
    renderBase();
    if (!products.length) {
      byId('cards').innerHTML = '<div class="empty">' +
        '<h3>За цими фільтрами товарів немає</h3>' +
        '<p class="muted">Спробуйте іншу назву, артикул або категорію.</p>' +
        '<button type="button" onclick="clearProductFilters()">Скинути всі фільтри</button></div>';
    }
    resultMessage();
  };

  async function fetchCatalog(nextPage, reset) {
    if (!reset && (loadingMore || products.length >= total)) return;
    const requestId = reset ? ++revision : revision;
    const filters = state();
    const more = byId('more');
    const cards = byId('cards');
    const status = byId('results-status');
    if (reset) {
      updateShareableLink(filters);
      more.classList.add('hidden');
    } else {
      loadingMore = true;
      more.disabled = true;
      more.textContent = 'Завантаження…';
    }
    status.textContent = 'Завантаження товарів…';
    status.setAttribute('aria-busy', 'true');
    cards.setAttribute('aria-busy', 'true');
    try {
      const params = new URLSearchParams({
        page: String(nextPage), limit: String(LIMIT),
        q: filters.q, category: filters.category,
        in_stock: String(filters.in_stock), sort: filters.sort
      });
      const response = await fetch('/api/products?' + params.toString(), {cache: 'no-store'});
      if (!response.ok) throw Error('Не вдалося отримати товари. Спробуйте повторити.');
      const result = await response.json();
      if (requestId !== revision) return; // A newer search has replaced this request.
      if (reset) products = result.items;
      else products = products.concat(result.items);
      total = result.total;
      page = nextPage;
      renderProducts();
    } catch (error) {
      if (requestId === revision) {
        status.textContent = 'Помилка завантаження каталогу';
        msg(error.message);
      }
    } finally {
      if (requestId === revision) {
        status.setAttribute('aria-busy', 'false');
        cards.setAttribute('aria-busy', 'false');
        more.disabled = false;
        more.textContent = 'Показати ще';
        loadingMore = false;
      }
    }
  }

  reload = function () {
    return fetchCatalog(1, true);
  };
  nextPage = function () {
    return fetchCatalog(page + 1, false);
  };

  window.clearProductFilters = function () {
    byId('search').value = '';
    byId('stock-only').checked = false;
    byId('sort').value = 'newest';
    const category = byId('category');
    category.value = '';
    // Updates the existing category-chip ARIA state and triggers one reload.
    category.dispatchEvent(new Event('change', {bubbles: true}));
  };
})();
