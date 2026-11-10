/* BeerShop: authenticated, server-authoritative pricing policy settings. */
(function () {
  'use strict';
  const fields = [
    'bank_card_rate', 'cash_usd_buy_rate', 'single_tax_percent',
    'military_percent', 'withdrawal_percent', 'profit_percent'
  ];
  const $price = name => document.getElementById('pricing-' + name);
  const output = () => document.getElementById('pricing-preview');
  const status = () => document.getElementById('pricing-status');

  function payload() {
    const body = {enabled: $price('enabled').checked,
                  confirm_rates: $price('confirmed').checked};
    for (const field of fields) {
      const input = $price(field);
      const val = input.value.trim();
      if (!val || !Number.isFinite(Number(val))) {
        throw Error('Заповніть числове поле: ' + input.closest('label').firstChild.textContent.trim());
      }
      body[field] = val;
    }
    return body;
  }
  function fmt(n) {
    return new Intl.NumberFormat('uk-UA', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(Number(n));
  }
  function showPreview(result) {
    const bank = Number(result.bank_card_rate), cash = Number(result.cash_usd_buy_rate);
    const site = Number(result.site_rate), percent = Number(result.markup_percent);
    output().textContent =
      'Картковий курс банку (введено вручну): ' + fmt(bank) + ' ₴/$\n' +
      'Курс купівлі готівкового USD (введено вручну): ' + fmt(cash) + ' ₴/$\n' +
      'Розрахунковий курс продажу BeerShop: ' + fmt(site) + ' ₴/$\n' +
      'Еквівалентна націнка до карткового курсу: ' + fmt(percent) + '%\n' +
      'Приклад продажу товару з базовою ціною $100: ' + fmt(site * 100) + ' ₴\n' +
      'Це розрахунок, а не автоматично оновлений банківський курс.\n' +
      'Націнка охоплює вказані відсоткові витрати та бажаний прибуток, але не ЄСВ, доставку й інші фіксовані витрати.';
  }
  window.pricingLoad = async function () {
    try {
      const policy = await api('/api/admin/pricing/policy');
      for (const field of fields) $price(field).value = policy[field] || '';
      $price('enabled').checked = Boolean(policy.enabled);
      $price('confirmed').checked = false;
      status().textContent = policy.enabled ?
        'АКТИВНО: розрахунковий курс використовується для цін USD у каталозі та замовленнях. Перевірено: ' +
        (policy.checked_at || 'невідомо') + '. Курси потрібно повторно підтвердити протягом 24 годин.' :
        'ВИМКНЕНО: магазин використовує старий офіційний курс НБУ. Увімкнення не відбувається автоматично.';
      output().textContent = 'Натисніть «Розрахувати», щоб переглянути результат без зміни цін.';
    } catch (error) {
      status().textContent = 'Не вдалося завантажити налаштування: ' + error.message;
    }
  };

  window.pricingPreview = async function (event) {
    event?.preventDefault();
    try {
      const values = payload();
      const response = await api('/api/admin/pricing/preview', {
        method: 'POST', body: JSON.stringify(values)
      });
      showPreview(response);
    } catch (error) {
      output().textContent = 'Помилка розрахунку: ' + error.message;
    }
  };

  window.pricingSave = async function (event) {
    event?.preventDefault();
    try {
      const values = payload();
      if (values.enabled && !values.confirm_rates) {
        throw Error('Перед активацією підтвердіть, що ви перевірили обидва реальні курси.');
      }
      if (values.enabled && !confirm(
        'Підтвердити зміну цін для ВСІХ USD-товарів у BeerShop? Перевірені курси діятимуть 24 години.')) return;
      const response = await api('/api/admin/pricing/policy', {
        method: 'PUT', body: JSON.stringify(values)
      });
      showPreview(response);
      status().textContent = response.enabled ?
        'Активовано. Дата й час перевірки: ' + response.checked_at :
        'Збережено, але політика вимкнена: чинний курс НБУ залишився без змін.';
      $price('confirmed').checked = false;
      msg('Валютні налаштування збережено');
    } catch (error) {
      status().textContent = 'Не збережено: ' + error.message;
      msg('Помилка: ' + error.message);
    }
  };
})();
