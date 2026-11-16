// BeerShop P1 isolated mobile checkout / UX / SEO / analytics / notifications.
const { chromium } = require('../frontend/node_modules/playwright');
const assert = require('node:assert/strict');
const baseURL = process.env.CRM_TEST_URL || 'http://127.0.0.1:8018';
const origin = new URL(baseURL);
assert(['127.0.0.1','localhost'].includes(origin.hostname), 'Synthetic localhost only');
const auth = { Authorization: 'Bearer ' + (process.env.CRM_TEST_TOKEN || 't'.repeat(40)) };

(async () => {
  const browser = await chromium.launch({
    headless:true, args:['--no-sandbox'],
    ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})
  });
  try {
    const context = await browser.newContext({
      baseURL, viewport:{width:390,height:844}, isMobile:true, hasTouch:true
    });
    await context.route('**/*', route =>
      new URL(route.request().url()).origin === origin.origin
        ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const api = async (method,path,data) => {
      const response = await context.request.fetch(path,{method,headers:auth,data});
      assert(response.ok(), path + ': ' + await response.text());
      return response.json();
    };
    const stamp = Date.now().toString();
    const cats = await api('GET', '/api/categories');
    const product = await api('POST','/api/admin/products',{
      category_id:cats[0].id,
      name:'P1 Mobile Checkout Synthetic ' + stamp,
      slug:'p1-mobile-'+stamp,
      description:'Тестовий товар для демонстрації мобільного оформлення замовлення.'
    });
    const variant = await api('POST','/api/admin/products/'+product.id+'/variants',{
      sku:'P1-'+stamp, price:'135.00',currency:'UAH',stock:5
    });
    const capabilities = await context.request.get('/api/p1/ux-capabilities');
    assert.equal((await capabilities.json()).analytics_enabled,true);

    for (const width of [320,360,390,600,768,1366]) {
      await page.setViewportSize({width,height:844});
      await page.goto('/');
      await page.locator('#cards .card').first().waitFor();
      assert.match(await page.locator('meta[name=description]').getAttribute('content'),/Обладнання|обладнання|Крани/);
      assert.equal(await page.locator('meta[property="og:type"]').getAttribute('content'),'website');
      await page.locator('#cart-toggle').click();
      await page.locator('#cart').waitFor({state:'visible'});
      assert.equal(await page.locator('#cart-toggle').getAttribute('aria-expanded'),'true');
      const info = await page.evaluate(() => ({
        viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
        cartWidth:document.getElementById('cart').clientWidth,
        cartScrollWidth:document.getElementById('cart').scrollWidth,
        inputSize:parseFloat(getComputedStyle(document.querySelector('#checkout [name=email]')).fontSize),
        inputHeight:document.querySelector('#checkout [name=email]').getBoundingClientRect().height,
        buttonHeight:document.getElementById('orderbtn').getBoundingClientRect().height
      }));
      assert(info.documentWidth <= width+1,'Document overflow at '+width+': '+JSON.stringify(info));
      assert(info.cartScrollWidth <= info.cartWidth+1,'Cart overflow '+JSON.stringify(info));
      assert(info.inputSize >= 16,'Mobile Safari must not zoom on form focus');
      assert(info.inputHeight >= 44 && info.buttonHeight >= 44,'Tap targets too small');
      if ([320,390].includes(width)) {
        await page.screenshot({path:'/tmp/beershop-p1-mobile-cart-'+width+'.png',fullPage:true});
      }
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#cart-toggle').getAttribute('aria-expanded'),'false');
    }

    await page.setViewportSize({width:390,height:844});
    const productResponse = await page.goto('/products/'+product.slug);
    assert.equal(productResponse.status(),200);
    assert.equal(productResponse.headers()['x-robots-tag'],'noindex, nofollow');
    await page.locator('#detail-add').waitFor();
    assert.match(await page.title(),/P1 Mobile Checkout Synthetic/);
    assert.match(await page.locator('meta[property="og:title"]').getAttribute('content'),/P1 Mobile Checkout Synthetic/);
    assert.match(await page.locator('meta[name=description]').getAttribute('content'),/Тестовий товар/);
    await page.locator('#detail-add').click();
    await page.locator('#cart').waitFor({state:'visible'});
    await page.locator('#cart').getByRole('button',{name:'Закрити кошик'}).click();
    await page.locator('#cart-toggle').click();
    // Native form validation must block an empty mobile checkout.
    await page.locator('#orderbtn').click();
    assert(await page.locator('#checkout [name=name]').evaluate(
      input => input.validity.valueMissing));
    await page.locator('#checkout [name=name]').fill('P1 Fake Buyer');
    await page.locator('#checkout [name=email]').fill('p1-'+stamp+'@example.test');
    await page.locator('#checkout [name=phone]').fill('+380501234567');
    await page.locator('#checkout [name=delivery_address]').fill('Тестовий склад, Київ');

    let orderRequests=0;
    page.on('request', req => {
      if (new URL(req.url()).pathname === '/api/orders' && req.method() === 'POST') orderRequests++;
    });
    let fakeQuote = true;
    await page.route('**/api/quote', async route => {
      if (!fakeQuote) return route.continue();
      fakeQuote=false;
      const real=await route.fetch();
      const body=await real.json();
      body.total=(Number(body.total)+7).toFixed(2);
      body.items=body.items.map(x=>({...x,unit_price_uah:(Number(x.unit_price_uah)+7).toFixed(2)}));
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    });

    await page.locator('#orderbtn').click();
    await page.waitForFunction(() =>
      document.querySelector('#p1-checkout-status')?.dataset.state === 'price_changed');
    assert.equal(orderRequests,0,'Price change should require another explicit confirmation');
    assert(await page.locator('#checkout [name=email]').inputValue() === 'p1-'+stamp+'@example.test');

    // Second submit reverts the injected test price. Buyer must confirm again.
    await page.locator('#orderbtn').click();
    await page.waitForFunction(() =>
      document.querySelector('#p1-checkout-status')?.dataset.state === 'price_changed');
    assert.equal(orderRequests,0);

    const placedPromise=page.waitForResponse(r =>
      new URL(r.url()).pathname === '/api/orders' && r.request().method() === 'POST');
    // Two rapid taps must generate only one order request.
    await page.locator('#orderbtn').evaluate(button => {
      button.click();
      button.click();
    });
    const placed=await placedPromise;
    assert.equal(placed.status(),201,await placed.text());
    assert.equal(orderRequests,1,'Duplicate checkout requests');
    await page.locator('#checkout-confirmation').waitFor({state:'visible'});
    await page.locator('.p1-confirmation-clarity').waitFor({state:'visible'});
    assert.match(await page.locator('#checkout-confirmation').textContent(),/Оплата ще не підтверджена/);
    assert.match(await page.locator('.p1-confirmation-clarity').textContent(),/SMS та email поки не надсилаються/);

    // Wait for asynchronous event-only requests, not an arbitrary sleep.
    await page.waitForFunction(async () => {
      const response = await fetch('/api/admin/p1/ux-report', {
        headers: { Authorization: 'Bearer ' + 't'.repeat(40) }
      });
      if (!response.ok) return false;
      const result = await response.json();
      return result.events.checkout_success >= 1 &&
             result.events.checkout_price_changed >= 2 &&
             result.events.checkout_attempt >= 3;
    });
    const metrics = await api('GET','/api/admin/p1/ux-report');
    assert.equal(metrics.mode,'SYNTHETIC_AGGREGATE_IN_MEMORY');
    assert(metrics.events.checkout_attempt >= 3);
    assert(metrics.events.checkout_price_changed >= 2);
    assert(metrics.events.checkout_success >= 1);
    assert(metrics.events.cart_add >= 1);
    assert(metrics.events.checkout_open >= 1);
    assert(metrics.events.product_view >= 1);
    assert.equal(metrics.pii_captured,false);
    assert.equal(metrics.unique_visitors,null);
    assert.equal(metrics.outbound_notifications,0);
    assert(!JSON.stringify(metrics).includes('p1-'+stamp+'@example.test'));
    const previews = await api('GET','/api/admin/p1/notification-previews');
    assert.equal(previews.mode,'TEMPLATES_ONLY_NO_DELIVERY');
    assert.equal(previews.messages_sent,0);
    assert.equal(previews.templates.length,3);

    const forbidden = await context.request.post('/api/p1/ux-event',{
      data:{event:'checkout_success',email:'should-never-be-accepted@example.test'}
    });
    assert.equal(forbidden.status(),422);
    await page.screenshot({path:'/tmp/beershop-p1-mobile-confirmation.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('BeerShop P1 passed: 320–1366 mobile cart, touch, price re-confirmation, onsite notice, no duplicate order, safe SEO, aggregate funnel and notification previews.');
  } finally {
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1});
