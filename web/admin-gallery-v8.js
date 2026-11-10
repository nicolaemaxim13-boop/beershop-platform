/* BeerShop v8: catalog32 photo editor. HTTPS URLs persist in Neon.
 * Existing local uploads stay explicitly temporary on Render Free.
 */
(function () {
  'use strict';
  let extras = [];
  const field = id => document.getElementById(id);

  function isAllowed(url) {
    if (url !== url.trim() || !url || url.length > 500 || /\s/.test(url)) return false;
    if (/^\/uploads\/[0-9a-f]{32}\.(jpg|png|webp)$/.test(url)) return true;
    try {
      const p = new URL(url);
      return p.protocol === 'https:' && !!p.hostname && !p.username &&
             !p.password && !p.hash && (!p.port || p.port === '443');
    } catch (_) { return false; }
  }
  function primary() { return field('c32image').value.trim(); }
  function urls() { return [primary(), ...extras].filter(Boolean); }

  function render() {
    const host = field('c32gallery'), gallery = urls();
    host.innerHTML = extras.map((url, index) => {
      const valid = isAllowed(url);
      const media = valid ? '<img src="' + esc(url) + '" alt="" loading="lazy">' :
        '<span aria-label="Фото недоступне" role="img">📦</span>';
      return '<div class="c32gallery-entry" data-index="' + index + '">' + media +
        '<div class="c32gallery-info"><small>Додаткове фото ' + (index + 1) +
        '</small><span title="' + esc(url) + '">' + esc(url) + '</span></div>' +
        '<div class="c32gallery-buttons">' +
        '<button type="button" class="secondary" data-action="main">Головне</button>' +
        '<button type="button" class="secondary" data-action="up" ' + (index === 0 ? 'disabled' : '') + ' aria-label="Фото вище">↑</button>' +
        '<button type="button" class="secondary" data-action="down" ' + (index === extras.length - 1 ? 'disabled' : '') + ' aria-label="Фото нижче">↓</button>' +
        '<button type="button" class="secondary" data-action="remove" aria-label="Видалити фото">✕</button>' +
        '</div></div>';
    }).join('') || '<p class="muted">Додаткових фото поки немає.</p>';
    field('c32gallery-count').textContent = gallery.length + ' із 6 фото · перше є головним';
    host.querySelectorAll('img').forEach(img => img.addEventListener('error', () => {
      const fallback = document.createElement('span');
      fallback.setAttribute('role', 'img');
      fallback.setAttribute('aria-label', 'Фото недоступне');
      fallback.textContent = '📦';
      img.replaceWith(fallback);
    }));
  }

  window.catalog32AddImage = function () {
    const url = field('c32newimage').value.trim();
    if (!isAllowed(url)) return msg('Потрібне коректне HTTPS-посилання (до 500 символів).');
    if (urls().includes(url)) return msg('Таке фото вже є в галереї.');
    if (urls().length >= 6) return msg('Максимум 6 фото для одного товару.');
    if (!primary()) { field('c32image').value = url; catalog32PreviewImage(); }
    else { extras.push(url); render(); }
    field('c32newimage').value = '';
  };

  field('c32gallery').addEventListener('click', event => {
    const button = event.target.closest('button[data-action]');
    const entry = button?.closest('[data-index]');
    if (!entry) return;
    const index = Number(entry.dataset.index), action = button.dataset.action;
    if (!Number.isInteger(index) || index < 0 || index >= extras.length) return;
    if (action === 'main') {
      const old = primary(), next = extras[index];
      field('c32image').value = next;
      if (old) extras[index] = old; else extras.splice(index, 1);
      catalog32PreviewImage();
    } else if (action === 'remove') extras.splice(index, 1);
    else if (action === 'up' && index > 0) [extras[index - 1], extras[index]] = [extras[index], extras[index - 1]];
    else if (action === 'down' && index + 1 < extras.length) [extras[index + 1], extras[index]] = [extras[index], extras[index + 1]];
    render();
  });

  const oldOpen = catalog32Open;
  catalog32Open = async function (id) {
    await oldOpen(id);
    if (!c32Selected || c32Selected.id !== id) return;
    extras = (Array.isArray(c32Selected.images) ? c32Selected.images.slice(1) : []).filter(Boolean);
    field('c32newimage').value = '';
    render();
  };

  const oldClose = catalog32Close;
  catalog32Close = function () { extras = []; oldClose(); };

  const oldPreview = catalog32PreviewImage;
  catalog32PreviewImage = function () { oldPreview(); render(); };

  catalog32Save = async function (event) {
    event.preventDefault();
    if (!c32Selected) return;
    const images = urls();
    if (images.length > 6 || new Set(images).size !== images.length ||
        images.some(url => !isAllowed(url))) return msg('Перевір URL фото, дублікати та ліміт 6.');
    if (!primary() && images.length) return msg('Вкажи головне фото.');
    const data = {
      name: field('c32name').value.trim(),
      description: field('c32description').value,
      category_id: Number(field('c32category').value),
      active: field('c32active').checked,
      images
    };
    if (data.name.length < 2) return msg('Вкажи назву товару.');
    field('c32save').disabled = true;
    try {
      await api('/api/admin/products/' + c32Selected.id, {
        method: 'PATCH', body: JSON.stringify(data)
      });
      msg('Товар і фотогалерею збережено у базі.');
      catalog32Close();
      await catalog32Load(c32Page);
      if (typeof loadProducts === 'function') await loadProducts();
    } catch (error) { msg('Помилка збереження галереї: ' + error.message); }
    finally { field('c32save').disabled = false; }
  };

  field('c32photo').addEventListener('error', () => {
    field('c32photo').hidden = true;
    field('c32imgmissing').hidden = false;
    field('c32imgmissing').textContent = 'Фото недоступне — заміни посилання';
  });
  field('c32cards').addEventListener('error', event => {
    if (!(event.target instanceof HTMLImageElement)) return;
    const img = event.target;
    if (!img.closest('.c32card')) return;
    const fallback = document.createElement('span');
    fallback.setAttribute('role', 'img');
    fallback.setAttribute('aria-label', 'Фото недоступне');
    fallback.textContent = '📦';
    img.replaceWith(fallback);
  }, true);
})();
