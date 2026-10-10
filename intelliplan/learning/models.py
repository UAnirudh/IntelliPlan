"""Additive learning tables, registered with the application's existing ORM."""
from datetime import datetime
from sqlalchemy import text as sql_text


def register(db):
    class Organization(db.Model):
        __tablename__ = 'learning_organizations'
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(160), nullable=False)
        kind = db.Column(db.String(16), nullable=False)
        parent_id = db.Column(db.Integer, db.ForeignKey('learning_organizations.id'))
        policy = db.Column(db.JSON, nullable=False, default=dict)
        version = db.Column(db.Integer, nullable=False, default=1)

    class Membership(db.Model):
        __tablename__ = 'learning_memberships'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
        organization_id = db.Column(db.Integer, db.ForeignKey('learning_organizations.id'), nullable=False)
        role = db.Column(db.String(16), nullable=False, default='student')
        __table_args__ = (db.UniqueConstraint('user_id', 'organization_id'),)

    class Audit(db.Model):
        __tablename__ = 'learning_policy_audit'
        id = db.Column(db.Integer, primary_key=True)
        organization_id = db.Column(db.Integer, db.ForeignKey('learning_organizations.id'), nullable=False, index=True)
        actor_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
        action = db.Column(db.String(64), nullable=False)
        changes = db.Column(db.JSON, nullable=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    class Course(db.Model):
        __tablename__ = 'learning_courses'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
        mode = db.Column(db.String(16), nullable=False)
        goal = db.Column(db.String(2000), nullable=False)
        blueprint = db.Column(db.JSON, nullable=False)
        state = db.Column(db.String(20), nullable=False, default='draft')
        revision = db.Column(db.Integer, nullable=False, default=1)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    class Lesson(db.Model):
        __tablename__ = 'learning_lessons'
        id = db.Column(db.Integer, primary_key=True)
        course_id = db.Column(db.Integer, db.ForeignKey('learning_courses.id', ondelete='CASCADE'), nullable=False, index=True)
        key = db.Column(db.String(64), nullable=False)
        content = db.Column(db.JSON, nullable=False)
        reviewed = db.Column(db.Boolean, nullable=False, default=False)
        __table_args__ = (db.UniqueConstraint('course_id', 'key'),)

    class Job(db.Model):
        __tablename__ = 'learning_generation_jobs'
        id = db.Column(db.Integer, primary_key=True)
        course_id = db.Column(db.Integer, db.ForeignKey('learning_courses.id', ondelete='CASCADE'), nullable=False, unique=True)
        state = db.Column(db.String(20), nullable=False, default='queued', index=True)
        attempts = db.Column(db.Integer, nullable=False, default=0)
        token_budget = db.Column(db.Integer, nullable=False, default=24000)
        reserved_tokens = db.Column(db.Integer, nullable=False, default=0)
        lease = db.Column(db.String(64))
        lease_until = db.Column(db.DateTime)
        error = db.Column(db.String(120))

    class Evidence(db.Model):
        __tablename__ = 'learning_evidence'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
        lesson_id = db.Column(db.Integer, db.ForeignKey('learning_lessons.id', ondelete='CASCADE'))
        session_id = db.Column(db.Integer, db.ForeignKey('learning_sittings.id', ondelete='CASCADE'))
        objective = db.Column(db.String(200), nullable=False)
        kind = db.Column(db.String(32), nullable=False)
        correct = db.Column(db.Boolean)
        assisted = db.Column(db.Boolean, nullable=False, default=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        __table_args__ = (db.UniqueConstraint('user_id', 'lesson_id'),)

    class Sitting(db.Model):
        __tablename__ = 'learning_sittings'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
        objective = db.Column(db.String(200), nullable=False)
        method = db.Column(db.String(32), nullable=False)
        planned_minutes = db.Column(db.Integer, nullable=False)
        intervention = db.Column(db.String(32), nullable=False)
        state = db.Column(db.String(16), nullable=False, default='active')
        elapsed_seconds = db.Column(db.Integer, nullable=False, default=0)
        revision = db.Column(db.Integer, nullable=False, default=1)
        resumed_at = db.Column(db.DateTime, default=datetime.utcnow)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        __table_args__ = (db.Index('uq_learning_open_sitting', 'user_id', unique=True,
            sqlite_where=sql_text("state IN ('active', 'paused')"),
            postgresql_where=sql_text("state IN ('active', 'paused')")),)

    return dict(Organization=Organization, Membership=Membership, Audit=Audit,
                Course=Course, Lesson=Lesson, Job=Job, Evidence=Evidence, Sitting=Sitting)
