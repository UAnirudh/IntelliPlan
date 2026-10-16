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


@dataclass(frozen=True)
class RevokedPeriod:
    user_id: int | None
    customer: str | None
    target_end: datetime | None
    kind: str


def _parse_uid(raw) -> int | None:
    try:
        if isinstance(raw, bool):
            return None
        uid = int(raw)
        return uid if uid > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_timestamp(raw) -> datetime | None:
    try:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            return None
        return datetime.fromtimestamp(raw, timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _known_prices():
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
    return known_prices


def _extract_invoice_periods(invoice, *, require_paid=True) -> list[PaidPeriod]:
    if require_paid and invoice.get('status') != 'paid':
        return []
    parent = invoice.get('parent') or {}
    details = parent.get('subscription_details') or invoice.get('subscription_details') or {}
    metadata = details.get('metadata') or invoice.get('metadata') or {}
    lines = invoice.get('lines') or {}
    if lines.get('has_more'):
        raise ValueError('Invoice requires complete subscription line items.')
    known_prices = _known_prices()
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
        uid = _parse_uid(meta.get('user_id'))
        finish = _parse_timestamp((line.get('period') or {}).get('end'))
        if uid is None or finish is None:
            raise ValueError('Paid invoice is missing a valid owner or service period.')
        periods.append(PaidPeriod(uid, plan_id, finish, invoice.get('customer')))
    if len(periods) > 1:
        raise ValueError('Paid invoice contains ambiguous subscription entitlements.')
    return periods


def paid_period(invoice) -> PaidPeriod | None:
    """Use a known non-prorated subscription line, never an invented month.

    Supports legacy invoice fields and the newer parent/pricing fields. A
    truncated or ambiguous payload is not sufficient entitlement evidence.
    """
    periods = _extract_invoice_periods(invoice, require_paid=True)
    return periods[0] if periods else None


def revoked_period(event) -> RevokedPeriod | None:
    """Extract subscription revocation evidence from verified cancellation, refund or dispute events."""
    kind = event.get('type')
    obj = (event.get('data') or {}).get('object') or {}
    if kind == 'customer.subscription.deleted':
        meta = obj.get('metadata') or {}
        uid = _parse_uid(meta.get('user_id'))
        customer = str(obj['customer'])[:64] if obj.get('customer') else None
        if uid is None and not customer:
            raise ValueError('Deleted subscription is missing owner and customer identity.')
        target_end = _parse_timestamp(obj.get('current_period_end'))
        if target_end is None:
            items = (obj.get('items') or {}).get('data') or []
            ends = [_parse_timestamp(item.get('current_period_end')) for item in items]
            valid_ends = [e for e in ends if e is not None]
            if valid_ends:
                target_end = max(valid_ends)
        return RevokedPeriod(uid, customer, target_end, 'subscription')

    if kind in {'invoice.payment_failed', 'invoice.marked_uncollectible', 'invoice.voided'}:
        periods = _extract_invoice_periods(obj, require_paid=False)
        if periods:
            p = periods[0]
            return RevokedPeriod(p.user_id, str(p.customer)[:64] if p.customer else None, p.end, 'invoice')
        parent = obj.get('parent') or {}
        details = parent.get('subscription_details') or obj.get('subscription_details') or {}
        meta = details.get('metadata') or obj.get('metadata') or {}
        uid = _parse_uid(meta.get('user_id'))
        customer = str(obj['customer'])[:64] if obj.get('customer') else None
        if uid is None and not customer:
            return None
        return RevokedPeriod(uid, customer, None, 'invoice')

    if kind == 'charge.refunded':
        if obj.get('refunded') is not True:
            amount = obj.get('amount')
            refunded = obj.get('amount_refunded')
            if not (type(amount) is int and type(refunded) is int and amount > 0 and refunded >= amount):
                return None
        meta = obj.get('metadata') or {}
        if meta.get('kind') == 'ai_topup':
            return None
        uid = _parse_uid(meta.get('user_id'))
        customer = str(obj['customer'])[:64] if obj.get('customer') else None
        if uid is None and not customer:
            raise ValueError('Refunded charge is missing owner and customer identity.')
        return RevokedPeriod(uid, customer, _parse_timestamp(meta.get('period_end')), 'refund')

    if kind in {'charge.dispute.created', 'charge.dispute.funds_withdrawn'}:
        meta = obj.get('metadata') or {}
        if meta.get('kind') == 'ai_topup':
            return None
        uid = _parse_uid(meta.get('user_id'))
        customer = str(obj['customer'])[:64] if obj.get('customer') else None
        if uid is None and not customer:
            raise ValueError('Disputed charge is missing owner and customer identity.')
        return RevokedPeriod(uid, customer, _parse_timestamp(meta.get('period_end')), 'dispute')

    return None
