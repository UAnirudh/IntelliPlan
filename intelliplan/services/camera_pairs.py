"""Physical expiry cleanup shared by the existing scheduled maintenance paths."""
from datetime import timedelta
from sqlalchemy import and_, or_

from time_utils import utcnow


def cleanup_camera_pairs():
    from App import StudyCameraPair, db
    from intelliplan.api.study_camera import LEASE_SECONDS
    now = utcnow()
    old = now - timedelta(seconds=LEASE_SECONDS)
    expired = StudyCameraPair.query.filter(or_(
        StudyCameraPair.expires_at <= now,
        StudyCameraPair.receiver_seen_at < old,
        and_(StudyCameraPair.connected.is_(True), StudyCameraPair.sender_seen_at < old),
    )).order_by(StudyCameraPair.id).with_for_update(skip_locked=True).limit(500).all()
    ids = [row.id for row in expired]
    removed = (StudyCameraPair.query.filter(StudyCameraPair.id.in_(ids)).delete(synchronize_session=False)
               if ids else 0)
    db.session.commit()
    return removed
