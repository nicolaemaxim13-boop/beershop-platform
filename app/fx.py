"""Official NBU USD/UAH parsing and deterministic, Decimal-only UAH prices."""
import json
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

URL = 'https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode=USD&json'
TWO = Decimal('0.01')


def fetch_nbu():
    request = Request(URL, headers={'User-Agent': 'BeerShop-Preview/3.4 (+exchange-rate-check)'})
    with urlopen(request, timeout=5) as reply:
        raw = reply.read(50000)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError('NBU payload is not a list')
    entry = next((x for x in data if isinstance(x, dict) and x.get('cc') == 'USD'), None)
    if entry is None:
        raise ValueError('USD missing in NBU response')
    rate = Decimal(str(entry['rate']))
    if not rate.is_finite() or not Decimal('10') < rate < Decimal('1000'):
        raise ValueError('Invalid USD/UAH rate')
    effective = datetime.strptime(entry['exchangedate'], '%d.%m.%Y').date()
    now = datetime.now(ZoneInfo('Europe/Kyiv')).date()
    if not now - timedelta(days=7) <= effective <= now + timedelta(days=1):
        raise ValueError('NBU date out of acceptable range')
    return rate.quantize(Decimal('0.000001')), effective.isoformat()


def convert_price(amount, currency, rate, baseline):
    amount, rate, baseline = map(Decimal, (str(amount), str(rate), str(baseline)))
    if not all(x.is_finite() and x > 0 for x in (rate, baseline)):
        raise ValueError('Invalid FX state')
    if not amount.is_finite() or amount < 0:
        raise ValueError('Invalid price')
    if currency == 'USD':
        raw = amount * rate
    elif currency == 'UAH':
        raw = amount * rate / baseline
    else:
        raise ValueError('Unsupported source currency')
    return raw.quantize(TWO, rounding=ROUND_HALF_UP)
