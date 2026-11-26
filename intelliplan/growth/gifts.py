"""One-time Pro gift purchases, separate from recurring subscriptions."""

import hashlib
from datetime import timedelta

from intelliplan.growth.plans import MONTHLY, PRO_PRICES_USD

MAX_MONTHS = 12
MONTH_DAYS = 30
UNIT_CENTS = PRO_PRICES_USD[MONTHLY] * 100


def gift_months(value):
    return next((months for months in range(1, MAX_MONTHS + 1) if str(months) == value), None)


def apply_gift_event(kind, obj, now):
    from App import ProGift, User, db

    paid = kind in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}
    revoked = kind in {"charge.refunded", "charge.dispute.created", "charge.dispute.funds_withdrawn"}
    if not (paid or revoked):
        return False
    metadata = obj["metadata"]
    months = gift_months(metadata.get("months"))
    raw_user = metadata.get("user_id")
    payment = obj.get("payment_intent")
    if (not months or not isinstance(raw_user, str) or not raw_user.isascii() or not raw_user.isdecimal()
            or len(raw_user) > 10 or not 0 < int(raw_user) <= 2_147_483_647):
        return False
    if not isinstance(payment, str) or not payment.startswith("pi_"):
        return False
    if paid and (obj.get("mode") != "payment" or obj.get("payment_status") != "paid"
                 or obj.get("currency") != "usd" or obj.get("amount_total") != UNIT_CENTS * months):
        return False
    if kind == "charge.refunded" and not obj.get("amount_refunded", 0):
        return False

    user = User.query.filter_by(id=int(raw_user)).with_for_update().first()
    if user is None:
        return False
    payment_ref = hashlib.sha256(payment.encode()).hexdigest()
    grant = db.session.get(ProGift, payment_ref)
    if grant:
        if grant.user_id != user.id:
            return False
        if revoked and not grant.revoked:
            grant.revoked = True
            db.session.commit()
        return True

    starts = ends = None
    if paid:
        latest = db.session.query(db.func.max(ProGift.ends_at)).filter(
            ProGift.user_id == user.id, ProGift.revoked.is_(False),
        ).scalar()
        starts = max(value for value in (now, latest, user.paid_until) if value is not None)
        ends = starts + timedelta(days=MONTH_DAYS * months)
    db.session.add(ProGift(payment_ref=payment_ref, user_id=user.id, starts_at=starts, ends_at=ends, revoked=revoked))
    db.session.commit()
    return True
