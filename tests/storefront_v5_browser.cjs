// Isolated local browser flow for catalog filters, comparison, product pages and checkout.
const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.CRM_TEST_URL || 'http://127.0.0.1:8018';
const origin = new URL(baseURL);
assert(['127.0.0.1', 'localhost'].includes(origin.hostname), 'Local fixture only');
const token = process.env.CRM_TEST_TOKEN || 't'.repeat(40);
(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
    args: ['--no-sandbox']
  });
  try {
    const context = await browser.newContext({ baseURL, viewport: { width: 390, height: 844 } });
    await context.route('**/*', route =>
      new URL(route.request().url()).origin === origin.origin ? route.continue() : route.abort()
    );
    const page = await context.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const api = async (method, path, data) => {
      const response = await context.request.fetch(path, {
        method, headers: {Authorization: 'Bearer ' + token}, data
      });
      assert(response.ok(), path + ': ' + await response.text());
      return response.json();
    };
    const stamp = Date.now().toString();
    const cats = await api('GET', '/api/categories');
    const category_id = cats[0].id;
    const mk = async (suffix, stock, price, imageUrl = '/assets/crm-icons/store.svg') => {
      const slug = 'v5-' + suffix.toLowerCase() + '-' + stamp;
      const product = await api('POST', '/api/admin/products', {
        name: 'V5 Fixture ' + suffix + ' ' + stamp, slug, category_id,
        image_url: imageUrl, description: 'Synthetic fixture, not real merchandise. '.repeat(14)
      });
      await api('POST', '/api/admin/products/' + product.id + '/variants', {
        sku: 'V5-' + suffix + '-' + stamp, price, stock, currency: 'UAH',
        attributes: {Type: suffix, Power: '120W'}
      });
      return {slug, product};
    };
    const available = await mk('A', 3, '1500.00');
    const unavailable = await mk('B', 0, '1200.00', 'https://images.prom.ua/broken-synthetic-image-for-ux-test.png');

    await page.goto('/');
    await page.locator('#search').fill('V5 Fixture');
    await page.waitForFunction(() =>
      document.querySelector('#results-status')?.getAttribute('aria-busy') === 'false' &&
      document.querySelector('#results-status')?.textContent.includes('пошук: «V5 Fixture»') &&
      [...document.querySelectorAll('#cards .card')].length === 2
    );
    const priceA = page.locator('.card').filter({hasText: available.product.name});
    const priceB = page.locator('.card').filter({hasText: unavailable.product.name});
    await priceA.waitFor();
    await priceB.waitFor();
    // A broken remote photo must show a compact fallback, never a huge alt-text
    // paragraph leaking over the product title or price.
    await priceB.scrollIntoViewIfNeeded();
    await priceB.locator('.image-error-fallback').waitFor();
    const badPhoto = await priceB.locator('.art').evaluate(el => ({
      height: el.clientHeight, scroll: el.scrollHeight,
      fallback: Boolean(el.querySelector('.image-error-fallback')),
      imgHidden: Boolean(el.querySelector('img[hidden]'))
    }));
    assert(badPhoto.fallback && badPhoto.imgHidden, 'Broken imported photo needs fallback: ' + JSON.stringify(badPhoto));
    assert(badPhoto.scroll <= badPhoto.height + 1, 'Broken photo overflows: ' + JSON.stringify(badPhoto));
    assert(await priceB.locator('.row button').isDisabled(), 'Sold-out item must not be added to cart');
    assert(await priceB.locator('.product-stock-badge').textContent() === '○ Немає в наявності');
    for (const width of [320, 390, 768, 1366, 1647, 1920]) {
      await page.setViewportSize({width, height: 844});
      const layout = await page.evaluate(() => {
        const card = document.querySelector('#cards .card');
        const image = card.querySelector('.art');
        return {
          viewport: innerWidth,
          docWidth: document.documentElement.scrollWidth,
          imageHeight: image.clientHeight,
          imageScrollHeight: image.scrollHeight,
          gridColumns: getComputedStyle(document.querySelector('#cards')).gridTemplateColumns.split(' ').length,
          titleClamp: getComputedStyle(card.querySelector('h3')).webkitLineClamp,
          descriptionClamp: getComputedStyle(card.querySelector('p.muted')).webkitLineClamp,
        };
      });
      assert(layout.docWidth <= width + 1, 'Catalog overflow ' + width + ': ' + JSON.stringify(layout));
      assert(layout.imageScrollHeight <= layout.imageHeight + 1, 'Art overflow ' + width + ': ' + JSON.stringify(layout));
      assert.equal(layout.titleClamp, '2');
      assert.equal(layout.descriptionClamp, '3');
      if (width === 1647) assert.equal(layout.gridColumns, 3, 'Avoid cramped four-column cards at user screenshot width');
      if ([390, 1647].includes(width)) {
        await page.screenshot({path: '/tmp/beershop-v6-catalog-' + width + '.png', fullPage: true});
      }
    }

    // v7: sorted server-side across all results, with shareable URL and reload.
    await page.locator('#sort').selectOption('price_asc');
    await page.waitForFunction(name =>
      document.querySelector('#cards .card h3')?.textContent.includes(name),
      unavailable.product.name
    );
    assert.equal(new URL(page.url()).searchParams.get('sort'), 'price_asc');
    assert.equal(new URL(page.url()).searchParams.get('q'), 'V5 Fixture');
    await page.reload();
    await page.locator('#cards .card').first().waitFor();
    assert.equal(await page.locator('#sort').inputValue(), 'price_asc');
    assert.equal(await page.locator('#search').inputValue(), 'V5 Fixture');
    assert((await page.locator('#cards .card').first().textContent()).includes(unavailable.product.name));

    await page.locator('#sort').selectOption('price_desc');
    await page.waitForFunction(name =>
      document.querySelector('#cards .card h3')?.textContent.includes(name),
      available.product.name
    );
    await page.locator('#search').fill('V5-B-' + stamp);
    await page.waitForFunction(name =>
      document.querySelectorAll('#cards .card').length === 1 &&
      document.querySelector('.card')?.textContent.includes(name),
      unavailable.product.name
    );
    await page.locator('#search').fill('V5 Fixture');
    await page.waitForFunction(() => document.querySelectorAll('#cards .card').length === 2);
    await page.locator('#sort').selectOption('newest');

    await page.locator('#stock-only').check();
    await page.waitForFunction(name =>
      document.querySelectorAll('#cards .card').length === 1 &&
      document.querySelector('#cards .card')?.textContent.includes(name),
      available.product.name
    );
    await page.locator('#stock-only').uncheck();
    await page.waitForFunction(() => document.querySelectorAll('#cards .card').length >= 2);

    await page.locator('.card').filter({hasText: available.product.name}).locator('.compare-toggle').click();
    await page.locator('.card').filter({hasText: unavailable.product.name}).locator('.compare-toggle').click();
    assert.equal(await page.locator('#compare-count').textContent(), '2');
    await page.locator('#compare-go').click();
    await page.locator('#compare-dialog').waitFor({state:'visible'});
    assert((await page.locator('#compare-content').textContent()).includes('120W'));
    assert.match(await page.locator('#compare-content').textContent(), /1[\s\u00a0]?201/);
    await page.getByRole('button', {name:'Закрити порівняння'}).click();

    for (const width of [320, 390, 768, 1366]) {
      await page.setViewportSize({width,height:844});
      await page.goto('/products/' + available.slug);
      await page.locator('#detail-variant').waitFor();
      assert.equal(await page.locator('#product-view h1').textContent(), available.product.name);
      assert.match(await page.locator('#detail-price').textContent(), /1[\s\u00a0]?501/);
      assert((await page.locator('#detail-attributes').textContent()).includes('120W'));
      const widthState = await page.evaluate(() => ({
        viewport:innerWidth, document:document.documentElement.scrollWidth
      }));
      assert(widthState.document <= widthState.viewport + 1,
        'Product page overflow: ' + width + ' ' + JSON.stringify(widthState));
      if (width === 390) await page.screenshot({path:'/tmp/beershop-v5-product-mobile.png',fullPage:true});
      if (width === 1366) await page.screenshot({path:'/tmp/beershop-v5-product-desktop.png',fullPage:true});
    }
    await page.locator('#detail-add').click();
    await page.locator('#cart').waitFor({state:'visible'});
    assert.equal(await page.locator('#checkout-count').textContent(), '1');
    await page.locator('#checkout [name=name]').fill('V5 Test Buyer');
    await page.locator('#checkout [name=email]').fill('v5-' + stamp + '@example.test');
    await page.locator('#checkout [name=phone]').fill('+380501234567');
    await page.locator('#checkout [name=delivery_address]').fill('Test branch 1 Kyiv');
    const orderReply = page.waitForResponse(r =>
      new URL(r.url()).pathname === '/api/orders' && r.request().method() === 'POST'
    );
    await page.locator('#orderbtn').click();
    const result = await orderReply;
    assert.equal(result.status(), 201, await result.text());
    const confirmation = page.locator('#checkout-confirmation');
    await confirmation.waitFor({state:'visible'});
    assert((await confirmation.textContent()).includes('Оплата ще не підтверджена'));
    assert.equal(await page.locator('#cart-toggle').getAttribute('aria-expanded'), 'false');
    assert.deepEqual(errors, []);
    console.log('BeerShop v5 storefront passed: stock filter, comparison, shareable product page, 320–1366px and verified checkout.');
  } finally {
    await browser.close();
  }
})().catch(e => {console.error(e);process.exitCode = 1;});
