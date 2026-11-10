"""Server-side BeerShop FX pricing policy: explicit fees, taxes and gross profit buffer.

Both source rates are administrator-entered, NOT live PrivatBank quotations.
The sale-rate is the amount of UAH required to replenish one USD of goods
after percentage-based taxes and optional cash-withdrawal charges.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

ONE = Decimal('1')
HUNDRED = Decimal('100')
SIX = Decimal('0.000001')
TWO = Decimal('0.01')


def calculate_sell_rate(bank_card_rate, cash_usd_buy_rate, single_tax_percent,
                        military_percent, withdrawal_percent, profit_percent):
    values = [Decimal(str(x)) for x in (bank_card_rate, cash_usd_buy_rate,
                                         single_tax_percent, military_percent,
                                         withdrawal_percent, profit_percent)]
    bank, cash, tax, levy, withdrawal, profit = values
    if not all(x.is_finite() for x in values):
        raise ValueError('Values must be finite')
    if not (Decimal('10') < bank < Decimal('1000') and
            Decimal('10') < cash < Decimal('1000')):
        raise ValueError('Bank and cash USD rates must be realistic positive values')
    if any(x < 0 for x in (tax, levy, withdrawal, profit)):
        raise ValueError('Negative percentages are not allowed')
    if tax + levy >= HUNDRED or withdrawal >= HUNDRED:
        raise ValueError('Tax and bank fee rates make conversion impossible')
    retained_revenue = (ONE - (tax + levy) / HUNDRED) * (ONE - withdrawal / HUNDRED)
    # Gross profit is specified as a surcharge on USD replacement cost.
    required_rate = cash * (ONE + profit / HUNDRED) / retained_revenue
    site_rate = required_rate.quantize(SIX, rounding=ROUND_CEILING)
    markup = ((site_rate / bank - ONE) * HUNDRED).quantize(TWO, rounding=ROUND_HALF_UP)
    return {
        'site_rate': site_rate,
        'markup_percent': markup,
        'bank_card_rate': bank,
        'cash_usd_buy_rate': cash,
        'tax_percent': tax,
        'military_percent': levy,
        'withdrawal_percent': withdrawal,
        'profit_percent': profit,
        'retained_fraction': retained_revenue,
    }


def round_retail_up(amount):
    """Round a customer-facing UAH unit price upwards to a whole hryvnia.

    For positive prices, avoid prices ending in the digit 0 (e.g. 500 -> 501).
    This is applied *after* source-currency conversion/FX margin. Preserve 0
    as zero rather than silently turning a free item into a charge. Accounting
    and order records still use Decimal with two fractional places.
    """
    price = Decimal(str(amount))
    if not price.is_finite() or price < 0:
        raise ValueError('Invalid retail price')
    if price == 0:
        return Decimal('0.00')
    rounded = price.quantize(ONE, rounding=ROUND_CEILING)
    if rounded % 10 == 0:
        rounded += ONE
    return rounded.quantize(TWO)
