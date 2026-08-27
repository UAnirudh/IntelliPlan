"""Encrypt third-party tokens that were stored before encryption existed.

New and updated rows encrypt themselves through ``secret_box.EncryptedText``.
Rows written earlier stay plaintext until something touches them, which for a
student who connected Canvas last term and has not reconnected since is never.
This walks them.

Safe to run repeatedly: an already-encrypted value is recognised by its
version prefix and skipped. Safe to run on a live database: each row is read,
re-written and committed individually, so a failure part-way leaves a mix of
encrypted and plaintext rows, which is exactly the state the code already
handles.

    DATA_ENCRYPTION_KEY=... python scripts/encrypt_existing_tokens.py
    DATA_ENCRYPTION_KEY=... python scripts/encrypt_existing_tokens.py --dry-run
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

import secret_box  # noqa: E402

#: (model name, columns). Names rather than classes so importing App stays
#: inside main(), where a failure can be reported instead of crashing import.
TARGETS = [
    ("GoogleIntegration", ["token_data"]),
    ("NotionIntegration", ["token"]),
    ("CanvasIntegration", ["access_token", "refresh_token"]),
    ("ClassroomIntegration", ["access_token", "refresh_token"]),
    ("BlackboardIntegration", ["access_token", "refresh_token"]),
    ("MoodleIntegration", ["ws_token"]),
]


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not secret_box.is_enabled():
        print("DATA_ENCRYPTION_KEY is not set (or is unusable), so there is "
              "nothing to encrypt with.")
        print("Generate one with:")
        print('  python -c "from cryptography.fernet import Fernet; '
              'print(Fernet.generate_key().decode())"')
        return 1

    import App
    from App import db

    total = 0
    changed = 0

    with App.app.app_context():
        for model_name, columns in TARGETS:
            model = getattr(App, model_name, None)
            if model is None:
                print(f"  {model_name}: not present, skipping")
                continue

            try:
                rows = model.query.all()
            except Exception as exc:
                print(f"  {model_name}: could not read ({exc})")
                continue

            touched = 0
            for row in rows:
                total += 1
                # Reading through the column type has already decrypted
                # anything encrypted, so a plain re-assignment re-encrypts on
                # write. Comparing the raw stored value is what tells us
                # whether that write is needed at all.
                raw = {c: _raw_value(db, model, row, c) for c in columns}
                needs = [c for c in columns
                         if raw[c] and not secret_box.is_encrypted(raw[c])]
                if not needs:
                    continue

                touched += 1
                changed += 1
                if dry_run:
                    continue

                try:
                    for column in needs:
                        # Re-assigning the same value leaves the attribute
                        # unchanged as far as SQLAlchemy is concerned, so no
                        # UPDATE is emitted and the row stays plaintext while
                        # the script reports success. Flagging it dirty is
                        # what actually forces the write through the
                        # encrypting column type.
                        setattr(row, column, getattr(row, column))
                        flag_modified(row, column)
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    print(f"  {model_name} id={row.id}: failed ({exc})")

            print(f"  {model_name}: {touched} of {len(rows)} row(s) "
                  f"{'would be' if dry_run else ''} encrypted")

    verb = "would encrypt" if dry_run else "encrypted"
    print(f"\n{verb} {changed} row(s) across {total} scanned.")
    if dry_run:
        print("Dry run — nothing was written.")
    return 0


def _raw_value(db, model, row, column):
    """The value as actually stored, bypassing transparent decryption."""
    from sqlalchemy import select
    try:
        stmt = select(getattr(model, column)).where(model.id == row.id)
        # ``EncryptedText`` decrypts on the way out, so compare on a plain
        # text cast instead of trusting the ORM value.
        return db.session.execute(
            stmt.with_only_columns(
                getattr(model.__table__.c, column).cast(db.Text))
        ).scalar()
    except Exception:
        return getattr(row, column, None)


if __name__ == "__main__":
    raise SystemExit(main())
