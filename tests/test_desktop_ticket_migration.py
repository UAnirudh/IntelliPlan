"""The additive epoch column preserves legacy tickets as issuance epoch zero."""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

from intelliplan.persistence.desktop import ensure_sql_ticket_epoch
from intelliplan.persistence.app_links import ensure_sql_link_epoch


def test_existing_desktop_ticket_table_is_upgraded_without_losing_tickets():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    db = SQLAlchemy(app)
    with app.app_context():
        db.session.execute(text('CREATE TABLE desktop_auth_codes (id INTEGER PRIMARY KEY, code_hash TEXT)'))
        db.session.execute(text("INSERT INTO desktop_auth_codes (id, code_hash) VALUES (42, 'legacy-hash')"))
        db.session.commit()
        ensure_sql_ticket_epoch(db)
        ensure_sql_ticket_epoch(db)
        assert db.session.execute(text(
            'SELECT id, code_hash, session_epoch FROM desktop_auth_codes')).one() == (42, 'legacy-hash', 0)


def test_existing_mobile_browser_ticket_table_is_upgraded_without_losing_tickets():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    db = SQLAlchemy(app)
    with app.app_context():
        db.session.execute(text('CREATE TABLE app_link_codes (id INTEGER PRIMARY KEY, code_hash TEXT)'))
        db.session.execute(text("INSERT INTO app_link_codes (id, code_hash) VALUES (42, 'legacy-mobile-hash')"))
        db.session.commit()
        ensure_sql_link_epoch(db)
        ensure_sql_link_epoch(db)
        assert db.session.execute(text(
            'SELECT id, code_hash, session_epoch FROM app_link_codes')).one() == (42, 'legacy-mobile-hash', 0)
