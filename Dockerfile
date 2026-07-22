# Deterministic container build for Railway.
#
# Why this exists: Nixpacks was intermittently producing a broken virtualenv
# whose Python symlink was dead, so the container crashed at start with
#   /app/.venv/bin/gunicorn: cannot execute: required file not found
# A plain Docker image installs gunicorn to /usr/local/bin with a valid
# interpreter, so that class of failure cannot occur. Running it via
# `python -m gunicorn` is belt-and-suspenders — it never touches a console
# script shebang at all.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer caches across source-only changes.
# Every requirement ships a manylinux wheel, so no build toolchain is needed.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Application source.
COPY . .

# Runtime-writable dirs (excluded from the build context) that the app may
# write to for uploads and filesystem sessions.
RUN mkdir -p instance uploads

# Railway injects $PORT at runtime; default keeps `docker run` working locally.
ENV PORT=8080
EXPOSE 8080

# Shell form so ${PORT} expands. Module invocation avoids any venv/console-
# script shebang dependency.
CMD python -m gunicorn App:app --bind 0.0.0.0:${PORT:-8080} --workers 4 --timeout 120 --max-requests 500 --max-requests-jitter 50
