/* BeerShop P1: accessible checkout cues + anonymous synthetic funnel only.
 * No cookies/storage, visitor IDs, IP logging, buyer fields or third-party SDKs.
 * The API is hard-disabled except in an isolated local browser test fixture.
 */
(function () {
  'use strict';
  let syntheticMetrics = false;
  let capabilitiesResolved = false;
  const pendingEvents = [];
  const events = new Set([
    'catalog_view', 'product_view', 'cart_add',
    'checkout_open', 'checkout_attempt', 'checkout_success',
    'checkout_error', 'checkout_price_changed'
  ]);
  const messages = {
    idle: 'Дані використовуємо лише для обробки замовлення.',
    checking: 'Перевіряємо актуальну суму й наявність. Не закривайте сторінку.',
    price_changed: 'Сума змінилася. Перегляньте оновлений кошик та натисніть підтвердження ще раз.',
    error: 'Замовлення не підтверджено. Перевірте помилку; введені контакти залишилися у формі.',
    success: 'Замовлення прийнято; оплата не списувалася. Очікуйте підтвердження менеджера.'
  };
  function postAnonymousEvent(name) {
    // Exact event enum only. Never serialize FormData, URL, cart, buyer info.
    fetch('/api/p1/ux-event', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({event: name})
    }).catch(() => { /* Never block a real checkout for test metrics. */ });
  }
  function event(name) {
    if (!events.has(name)) return;
    if (!capabilitiesResolved) {
      // Only a small event-name queue. Never hold forms, URL, contact values,
      // product names, quantities, identifiers or arbitrary user data.
      if (pendingEvents.length < 20) pendingEvents.push(name);
      return;
    }
    if (syntheticMetrics) postAnonymousEvent(name);
  }
  function status(name) {
    const el = document.getElementById('p1-checkout-status');
    if (!el || !Object.hasOwn(messages, name)) return;
    el.dataset.state = name;
    el.textContent = messages[name];
  }
  window.BeerShopP1 = Object.freeze({event, status});

  const cart = document.getElementById('cart');
  const trigger = document.getElementById('cart-toggle');
  if (cart && trigger) {
    trigger.addEventListener('click', () => {
      // The existing inline click handler toggles the panel first.
      if (cart.getAttribute('aria-hidden') === 'false') event('checkout_open');
    });
  }

  document.addEventListener('click', click => {
    const button = click.target.closest?.('button');
    if (!button || button.disabled) return;
    if (button.matches('#detail-add, #cards .card .row button[onclick^="add("]')) {
      event('cart_add');
    }
  });

  const confirmation = document.getElementById('checkout-confirmation');
  if (confirmation) {
    let shown = false;
    new MutationObserver(() => {
      const open = !confirmation.classList.contains('hidden');
      if (!open) { shown = false; return; }
      if (shown) return;
      shown = true;
      const next = document.createElement('small');
      next.className = 'p1-confirmation-clarity';
      next.textContent = 'Автоматичні SMS та email поки не надсилаються. Статус оплати підтвердить менеджер.';
      confirmation.appendChild(next);
    }).observe(confirmation, {childList: true, attributes: true, attributeFilter: ['class']});
  }

  // One anonymous synthetic event per page view. No analytics request
  // at all when this test-only feature is disabled on production.
  fetch('/api/p1/ux-capabilities', {cache: 'no-store'})
    .then(resp => resp.ok ? resp.json() : {analytics_enabled: false})
    .then(data => {
      syntheticMetrics = data.analytics_enabled === true;
      capabilitiesResolved = true;
      if (!syntheticMetrics) {
        pendingEvents.length = 0;
        return;
      }
      pendingEvents.splice(0).forEach(postAnonymousEvent);
      const caption = document.getElementById('p1-ux-privacy');
      if (caption) {
        caption.hidden = false;
        caption.textContent = 'Тестовий режим: лише знеособлені лічильники подій, без cookie, IP та контактів.';
      }
      event(location.pathname.startsWith('/products/') ? 'product_view' : 'catalog_view');
    }).catch(() => {
      syntheticMetrics = false;
      capabilitiesResolved = true;
      pendingEvents.length = 0;
    });
  status('idle');
})();
