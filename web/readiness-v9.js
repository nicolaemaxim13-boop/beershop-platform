/* v9 staging audit: DOM textContent only; no data mutation or external calls. */
(function () {
  'use strict';
  const byId = id => document.getElementById(id);
  const labels = {pass:'Пройдено', warn:'Увага', block:'Блокує запуск'};
  const names = {
    security:'Безпека',data:'Дані й відновлення',pricing:'Валюта й ціни',
    iban:'Реквізити й платежі',inventory:'Склад',operations:'Замовлення й доставка',
    legal:'Юридична готовність',catalog:'Каталог'
  };
  const add = (parent,tag,className,text) => {
    const node=document.createElement(tag);
    if (className) node.className=className;
    if (text!==undefined) node.textContent=String(text);
    parent.append(node);
    return node;
  };
  window.readiness9Load = async function () {
    const status=byId('readiness9-status');
    const metrics=byId('readiness9-metrics');
    const host=byId('readiness9-checks');
    status.textContent='Завантаження аудиту…';
    metrics.replaceChildren();
    host.replaceChildren();
    try {
      const audit=await api('/api/admin/v9/readiness');
      const count=audit.summary;
      if (!audit || !Array.isArray(audit.checks) || !count) throw Error('Неповні дані аудиту');
      status.textContent='НЕ ГОТОВО ДО РЕАЛЬНИХ ПРОДАЖІВ · '+audit.checks.length+
        ' перевірок · '+new Date(audit.checked_at).toLocaleString('uk-UA');
      for (const [label,value] of [
        ['Критичні бар’єри',count.blockers],
        ['Попередження',count.warnings],
        ['Пройдено',count.passed],
        ['Опубліковані товари',count.published_products],
        ['Доступні SKU',count.available_skus],
        ['Неоплачені замовлення',count.unpaid_orders]
      ]) {
        const card=add(metrics,'div','readiness-metric');
        add(card,'span','readiness-metric-number',value);
        add(card,'span','readiness-metric-label',label);
      }
      const groups=new Map();
      for(const check of audit.checks){
        if(!groups.has(check.group))groups.set(check.group,[]);
        groups.get(check.group).push(check);
      }
      for(const [group,checks] of groups) {
        const section=add(host,'div','readiness-group');
        add(section,'h3','',names[group]||group);
        for(const check of checks){
          const item=add(section,'article','readiness-check '+check.status);
          const header=add(item,'div','readiness-check-head');
          add(header,'strong','',check.title);
          add(header,'span','readiness-badge '+check.status,labels[check.status]||check.status);
          add(item,'p','readiness-detail',check.detail);
          add(item,'p','readiness-action','Далі: '+check.action);
        }
      }
      add(host,'p','readiness-footer',
        'Звіт не засвідчує фактичну наявність товарів, власність банківського рахунку або юридичну відповідність. Жодних платежів чи банківських API не викликано.');
    } catch (error) {
      status.textContent='Помилка читання аудиту: '+error.message;
    }
  };
})();
