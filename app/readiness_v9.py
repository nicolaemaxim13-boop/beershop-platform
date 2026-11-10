"""BeerShop v9 — read-only sales-readiness audit.

The report is *not* a certification that a store may take real payments.
No bank connections, side effects, personal records or secret values are used.
"""
import os
import re
import secrets
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import func, select


def valid_ukrainian_iban(value: str) -> bool:
    """ISO 13616 Mod-97 and UA length/BBAN shape, not proof of bank ownership."""
    iban = value.strip().replace(' ', '').upper()
    if not re.fullmatch(r'UA[0-9]{27}', iban):
        return False
    # BBAN(25 digits) followed by country UA=3010, then check digits.
    rotated = iban[4:] + '3010' + iban[2:4]
    remainder = 0
    for digit in rotated:
        remainder = (remainder * 10 + int(digit)) % 97
    return remainder == 1


def recent_manual_rate(policy, now):
    if not policy or not policy.enabled:
        return False
    try:
        checked = datetime.fromisoformat(policy.checked_at)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        return timedelta(0) <= now - checked <= timedelta(hours=24)
    except (ValueError, TypeError, OverflowError):
        return False


def build_report(db, *, Product, Variant, Order, OrderItem, PricingPolicy,
                 Reservation, Movement, admin_token: str, iban: str, payee: str,
                 persistent_store: bool, now=None):
    """Snapshot only. Never automatically mark legal/supplier/bank facts verified."""
    now = now or datetime.now(timezone.utc)
    count = lambda statement: int(db.scalar(statement) or 0)
    products = count(select(func.count()).select_from(Product).where(Product.store_id == 1))
    active = count(select(func.count()).select_from(Product).where(
        Product.store_id == 1, Product.active.is_(True)))
    sku_count = count(select(func.count()).select_from(Variant).where(Variant.store_id == 1))
    available_skus = count(select(func.count()).select_from(Variant).where(
        Variant.store_id == 1, Variant.active.is_(True), Variant.stock > 0))
    negative_stock = count(select(func.count()).select_from(Variant).where(
        Variant.store_id == 1, Variant.stock < 0))
    zero_cost_skus = count(select(func.count()).select_from(Variant).where(
        Variant.store_id == 1, Variant.active.is_(True), Variant.price <= 0))
    usd_skus = count(select(func.count()).select_from(Variant).where(
        Variant.store_id == 1, Variant.currency == 'USD'))
    uah_skus = count(select(func.count()).select_from(Variant).where(
        Variant.store_id == 1, Variant.currency == 'UAH'))
    missing_images = count(select(func.count()).select_from(Product).where(
        Product.store_id == 1, Product.active.is_(True),
        (Product.image_url == '') | Product.image_url.is_(None)))
    reserved_lines = count(select(func.count()).select_from(Reservation).where(
        Reservation.state == 'reserved'))
    reserved_units = count(select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
        Reservation.state == 'reserved'))
    # Reservations should correspond to an existing order line with equal qty.
    mismatches = count(select(func.count()).select_from(Reservation).
        outerjoin(OrderItem, OrderItem.id == Reservation.item_id).
        outerjoin(Order, Order.id == Reservation.order_id).
        where((OrderItem.id.is_(None)) | (Order.id.is_(None)) |
              (OrderItem.order_id != Reservation.order_id) |
              (OrderItem.variant_id != Reservation.variant_id) |
              (OrderItem.quantity != Reservation.quantity)))
    orders = count(select(func.count()).select_from(Order).where(Order.store_id == 1))
    unpaid = count(select(func.count()).select_from(Order).where(
        Order.store_id == 1, Order.payment_status == 'unpaid',
        Order.status != 'cancelled'))
    policy = db.get(PricingPolicy, 1)
    policy_on = bool(policy and policy.enabled)
    rate_fresh = recent_manual_rate(policy, now)
    iban_valid = valid_ukrainian_iban(iban) if iban else False
    payee_valid = len(payee.strip()) >= 5
    admin_token_ok = len(admin_token) >= 32

    checks = []
    def add(key, group, status, title, detail, action):
        checks.append({'id': key, 'group': group, 'status': status,
                       'title': title, 'detail': detail, 'action': action})

    add('admin_token', 'security', 'pass' if admin_token_ok else 'block',
        'Адміністративний токен',
        'Секрет налаштовано (його значення не повертається).' if admin_token_ok
        else 'Секрет адміністратора відсутній або коротший за 32 символи.',
        'Використовувати довгий секрет лише у Render Environment.')
    add('individual_accounts', 'security', 'block', 'Ролі та 2FA',
        'Наразі всі адміністратори використовують один токен; немає персональних ролей, 2FA та відкликання доступу.',
        'Реалізувати персональні входи, мінімальні дозволи та MFA перед продажами.')
    add('abuse_protection', 'security', 'block', 'Захист від зловживань',
        'Не підтверджені розподілені обмеження частоти запитів, сповіщення та production security review.',
        'Додати перевірені ліміти/моніторинг і провести незалежний огляд.')
    add('persistent_db', 'data', 'pass' if persistent_store else 'block',
        'Постійна база даних',
        'Постійне підключення налаштовано.' if persistent_store else
        'BEERSHOP_PERSISTENT_DATABASE_URL відсутній; дані можуть бути тимчасовими.',
        'Не перемикати робочу БД без перевіреного плану міграції.')
    add('backup_restore', 'data', 'block', 'Відновлення з резервної копії',
        'JSON-експорт існує, але автоматичні зашифровані копії та тест відновлення не підтверджені.',
        'Налаштувати backup і перевірити відновлення на окремій тестовій базі.')
    add('migration_process', 'data', 'block', 'Версійні міграції',
        'Застосунок досі використовує SQLAlchemy create_all при старті, без повного Alembic-циклу.',
        'Запровадити контрольовані та протестовані міграції зі шляхом відкату.')
    add('fx_pricing', 'pricing',
        'pass' if policy_on and rate_fresh else 'block',
        'Валютна націнка й актуальність курсу',
        ('Ручну політику активовано; вказаний курс перевірено протягом 24 годин.'
         if policy_on and rate_fresh else
         'Підтверджена ручна політика неактивна або курс старший за 24 години. Курс НБУ не гарантує покриття витрат.'),
        'Увімкнути політику тільки після перевірки реальних ставок та курсу.')
    if policy_on:
        add('fixed_costs', 'pricing', 'warn', 'Постійні витрати й прибуток',
            'Калькулятор не враховує ЄСВ, доставку, повернення та інші фіксовані витрати.',
            'Погодити фінансову модель і ставки з бухгалтером.')
    add('sku_cost', 'pricing', 'block' if zero_cost_skus else 'pass',
        'Ціни SKU',
        f'SKU з нульовою чи від’ємною базовою ціною: {zero_cost_skus}.',
        'Виправити всі невалідні ціни до реальних продажів.')
    if uah_skus:
        add('uah_fx_cost_basis', 'pricing', 'warn', 'Собівартість UAH-товарів',
            'Частина цін імпортована в UAH і не отримує відсоткової націнки USD вдруге.',
            'Звірити собівартість та прибуток кожного UAH-SKU.')
    add('inventory_consistency', 'inventory', 'pass' if negative_stock == 0 and mismatches == 0 else 'block',
        'Математична цілісність резервів',
        f'Від’ємні залишки: {negative_stock}; невідповідні резерви: {mismatches}.',
        'Перед запуском усунути розбіжності й повторити звірку.')
    add('physical_stock', 'inventory', 'block', 'Фізичний склад',
        f'Доступних SKU: {available_skus}, відкритих резервів: {reserved_lines}. '
        'Цифри БД не доводять наявності товару в постачальника.',
        'Провести і документально підтвердити фактичний перерахунок.')
    add('bank_details', 'iban', 'warn' if iban_valid and payee_valid else 'block',
        'Реквізити IBAN',
        ('Синтаксис та контрольна сума UA IBAN правильні; право власності на рахунок не підтверджене.'
         if iban_valid and payee_valid else 'Український IBAN або ім’я отримувача відсутні чи некоректні.'),
        'Звірити реквізити з банком, договором і даними ФОП.')
    add('bank_confirmation', 'iban', 'block', 'Звірка реальних надходжень',
        'Платежі підтверджуються оператором вручну, а не перевіреною банківською випискою через API.',
        'Перевірити процедуру виписки, часткових оплат, повернень та повторів. Нічого автоматично не вмикати.')
    add('open_orders', 'operations', 'warn' if unpaid else 'pass',
        'Неоплачені замовлення',
        f'Активних неоплачених замовлень: {unpaid}.',
        'Звірити їх із реальними замовленнями перед запуском.')
    add('shipping', 'operations', 'warn', 'Доставка',
        'Створення ТТН автоматично не реалізовано; статус може бути підтверджений вручну.',
        'Перевірити договір із перевізником, реальну ТТН і сценарій повернення.')
    add('consumer_law', 'legal', 'block', 'Документи продавця та фіскалізація',
        'Не підтверджено оферту, політику приватності, повернення, дані ФОП та вимоги до фіскальних чеків.',
        'Перевірити юридичні й бухгалтерські вимоги до реального продажу в Україні.')
    add('photostorage', 'catalog', 'warn' if missing_images == 0 else 'block',
        'Фотографії каталогу',
        f'Опублікованих товарів без URL фото: {missing_images}; зовнішні URL можуть стати недоступними.',
        'Перевірити права, доступність та надійність зберігання зображень.')

    counters = {kind: sum(1 for c in checks if c['status'] == kind)
                for kind in ('pass', 'warn', 'block')}
    return {
        'mode': 'READ_ONLY_AUDIT',
        'sales_ready': False if counters['block'] else None,  # Never self-certify legal/bank readiness.
        'sales_ready_note': 'Ні. Звіт не підтверджує право приймати реальні платежі.',
        'checked_at': now.isoformat(),
        'summary': {
            'products': products, 'published_products': active,
            'skus': sku_count, 'available_skus': available_skus,
            'usd_skus': usd_skus, 'uah_skus': uah_skus,
            'orders': orders, 'unpaid_orders': unpaid,
            'reserved_lines': reserved_lines, 'reserved_units': reserved_units,
            'blockers': counters['block'], 'warnings': counters['warn'],
            'passed': counters['pass']
        },
        'checks': checks,
        'external_payment_calls': 0,
        'mutations': 0
    }
