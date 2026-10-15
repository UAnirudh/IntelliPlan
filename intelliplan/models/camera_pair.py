"""Short-lived, owner-bound WebRTC signaling; never holds video or audio."""
from time_utils import utcnow


def register(db):
    for mapper in db.Model.registry.mappers:
        if getattr(mapper.class_, '__tablename__', '') == 'study_camera_pairs':
            return mapper.class_

    class StudyCameraPair(db.Model):
        __tablename__ = 'study_camera_pairs'

        id = db.Column(db.String(32), primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                            nullable=False, unique=True)
        receiver_hash = db.Column(db.String(64), nullable=False)
        invite_hash = db.Column(db.String(64))
        sender_hash = db.Column(db.String(64))
        offer = db.Column(db.Text)
        answer = db.Column(db.Text)
        created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
        expires_at = db.Column(db.DateTime, nullable=False, index=True)
        receiver_seen_at = db.Column(db.DateTime, nullable=False)
        sender_seen_at = db.Column(db.DateTime)
        connected = db.Column(db.Boolean, nullable=False, default=False)

    return StudyCameraPair
