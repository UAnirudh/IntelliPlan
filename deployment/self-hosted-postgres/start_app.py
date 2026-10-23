"""Optional same-PC app launcher: credentials stay out of the image and argv."""
import os
from pathlib import Path
from urllib.parse import quote


def main():
    password = Path('/run/secrets/app_password').read_text().strip()
    session_key = Path('/run/secrets/session_key').read_text().strip()
    if len(password) < 32 or len(session_key) < 32:
        raise SystemExit('Self-hosted secrets are missing or too short; run setup first.')
    os.environ['DATABASE_URL'] = 'postgresql+psycopg2://intelliplan:' + quote(password, safe='') + '@db:5432/intelliplan'
    os.environ['SECRET_KEY'] = session_key
    os.execvp('python', ['python', '-m', 'gunicorn', 'App:app', '-c', 'gunicorn.conf.py'])


if __name__ == '__main__':
    main()
