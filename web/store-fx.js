/* BeerShop 3.4: public UAH quote and checkout confirmation. The server owns the FX calculations. */
async function fxBanner(){
  const banner=document.getElementById('fx-info');
  if(!banner)return;
  try{
    const res=await fetch('/api/fx/usd-uah',{cache:'no-store'});
    const fx=await res.json();
    if(!res.ok)throw Error(fx.detail || 'Курс недоступний');
    const rate=new Intl.NumberFormat('uk-UA',{minimumFractionDigits:2,maximumFractionDigits:4}).format(Number(fx.rate));
    banner.textContent='Ціни у гривнях · 1 USD = '+rate+' ₴ · курс НБУ на '+fx.effective_date+(fx.delayed?' (останній перевірений курс)':'');
  }catch(e){banner.textContent='Оновлення курсу недоступне. Оформлення замовлень тимчасово призупинено.';}
}
placeOrder=async function(e){
  e.preventDefault();
  if(!Object.keys(cart).length){msg('Кошик порожній');return}
  const btn=document.getElementById('orderbtn');btn.disabled=true;
  try{
    const items=Object.entries(cart).map(([id,item])=>({variant_id:Number(id),quantity:item.qty}));
    const originalTotal=Object.values(cart).reduce((x,item)=>x+item.price*item.qty,0);
    const quoteResp=await fetch('/api/quote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items}),cache:'no-store'});
    const quote=await quoteResp.json();
    if(!quoteResp.ok)throw Error(quote.detail || 'Не вдалося перевірити курс і наявність');
    for(const line of quote.items){
      if(cart[line.variant_id])cart[line.variant_id].price=Number(line.unit_price_uah);
    }
    renderCart();
    if(Math.round(originalTotal*100)!==Math.round(Number(quote.total)*100)){
      msg('Курс USD/UAH змінився, ціни кошика оновлено. Перевірте суму і підтвердьте замовлення ще раз.');
      fxBanner();return;
    }
    if(!pendingKey)pendingKey=crypto.randomUUID();
    const data=Object.fromEntries(new FormData(e.target));
    data.request_key=pendingKey;data.items=items;data.expected_total=quote.total;
    const orderResp=await fetch('/api/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const order=await orderResp.json();
    if(!orderResp.ok)throw Error(order.detail || 'Помилка замовлення');
    cart={};pendingKey=null;renderCart();toggleCart(false);e.target.reset();
    const confirmation=document.getElementById('checkout-confirmation');
    if(confirmation){
      confirmation.textContent='Замовлення №'+order.id+' прийнято на суму '+fmt(Number(order.total))+'. Менеджер зв’яжеться з вами. Оплата ще не підтверджена.';
      confirmation.classList.remove('hidden');
      confirmation.scrollIntoView({block:'start',behavior:'auto'});
    }
    msg('Замовлення №'+order.id+' створено. Менеджер зв’яжеться з вами.');
    reload();
  }catch(error){msg(error.message)}finally{btn.disabled=false}
};
fxBanner();
setInterval(fxBanner, 30*60*1000);
