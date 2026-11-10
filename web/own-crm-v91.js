/* v9.1: first-party BeerShop CRM is authoritative.
 * Hide legacy KeyCRM controls only in STRICT ISOLATED STAGING.
 * This file never reads credentials, sends customer data or touches the DB.
 */
(async function () {
  'use strict';
  try {
    const response = await fetch('/api/v91/features', {cache: 'no-store'});
    if (!response.ok) return;
    const config = await response.json();
    if (!config.own_crm_only) return;
    const hide = id => {
      const node = document.getElementById(id);
      if (node) {
        node.hidden = true;
        node.setAttribute('aria-hidden', 'true');
        node.querySelectorAll('button,input,select,textarea,a').forEach(element => {
          if ('disabled' in element) element.disabled = true;
          element.setAttribute('tabindex', '-1');
        });
      }
    };
    const show = id => {
      const node = document.getElementById(id);
      if (node) {
        node.hidden = false;
        node.removeAttribute('aria-hidden');
      }
    };
    hide('opsExternalCrmLegacy');
    hide('conn41ExternalCrmLegacy');
    show('opsOwnCrmV91');
    show('conn41OwnCrmV91');
    const title = document.querySelector('#connectors41 h2');
    if (title) title.textContent = 'BeerShop 4.1 · Доставка і банк';
    const notice = document.querySelector('#connectors41 .panel > p.muted');
    if (notice) notice.textContent =
      'Власна BeerShop CRM керує клієнтами й замовленнями. Зовнішні CRM вимкнені; банківські платежі та API доставки в staging не виконуються.';
    const ops = document.querySelector('#ops40 .panel > p.muted');
    if (ops) ops.textContent =
      'Платежі, власна BeerShop CRM й Нова пошта. Реальні банківські операції та зовнішня синхронізація в staging вимкнені.';
  } catch (_) {
    // No security decisions depend on UI state: the server blocks all legacy
    // KeyCRM operations when strict RBAC is on.
  }
})();
