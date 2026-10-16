"""Extract paid subscription periods from verified Stripe invoice events."""
from dataclasses import dataclass
from datetime import datetime, timezone
import os

from intelliplan.premium import catalog


@dataclass(frozen=True)
class PaidPeriod:
    user_id: int
    plan: str
    end: datetime
    customer: str | None


def paid_period(invoice) -> PaidPeriod | None:
    """Use a known non-prorated subscription line, never an invented month.

    Supports legacy invoice fields and the newer parent/pricing fields. A
    truncated or ambiguous payload is not sufficient entitlement evidence.
    """
    if invoice.get('status') != 'paid':
        return None
    parent = invoice.get('parent') or {}
    details = parent.get('subscription_details') or invoice.get('subscription_details') or {}
    metadata = details.get('metadata') or {}
    lines = invoice.get('lines') or {}
    if lines.get('has_more'):
        raise ValueError('Paid invoice requires complete subscription line items.')
    known_prices = {}
    def add_price(value, name):
        if value in known_prices:
            raise ValueError('Stripe recurring price configuration is ambiguous.')
        known_prices[value] = name

    for name, variable in [('pro', 'STRIPE_PRICE_ID'), ('pro', 'STRIPE_PRICE_ID_YEARLY')]:
        if value := os.getenv(variable, '').strip():
            add_price(value, name)
    for plan in catalog.plans().values():
        for interval in (catalog.MONTHLY, catalog.YEARLY):
            if value := plan.stripe_price_id(interval):
                add_price(value, plan.id)
    periods = []
    for line in lines.get('data') or []:
        line_parent = (line.get('parent') or {}).get('subscription_item_details') or {}
        if line.get('type') != 'subscription' and not line_parent:
            continue
        if line.get('proration') or line_parent.get('proration'):
            continue
        price = (line.get('price') or {}).get('id') or (
            ((line.get('pricing') or {}).get('price_details') or {}).get('price'))
        plan_id = known_prices.get(price)
        if not plan_id:
            continue
        meta = metadata or line.get('metadata') or {}
        if meta.get('plan') and meta['plan'] != plan_id:
            raise ValueError('Paid invoice plan does not match its configured price.')
        try:
            uid = int(meta.get('user_id'))
            end = (line.get('period') or {}).get('end')
            if uid <= 0 or isinstance(end, bool) or not isinstance(end, int) or end <= 0:
                raise ValueError
            finish = datetime.fromtimestamp(end, timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError, OSError):
            raise ValueError('Paid invoice is missing a valid owner or service period.') from None
        periods.append(PaidPeriod(uid, plan_id, finish, invoice.get('customer')))
    if len(periods) > 1:
        raise ValueError('Paid invoice contains ambiguous subscription entitlements.')
    return periods[0] if periods else None
