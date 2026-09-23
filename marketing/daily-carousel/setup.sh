#!/usr/bin/env bash
# Installs the three typefaces the carousel is set in, so headless Chromium
# renders them locally instead of silently falling back to a system font.
set -euo pipefail
FONT_DIR="${HOME}/.local/share/fonts"
mkdir -p "$FONT_DIR"
fetch() {
  local css; css="$(curl -fsS -A 'Mozilla/5.0' "https://fonts.googleapis.com/css2?family=$1&display=swap")"
  local i=0
  for u in $(grep -o 'https://fonts.gstatic.com[^)]*' <<<"$css"); do
    i=$((i+1)); curl -fsS "$u" -o "$FONT_DIR/$2-$i.ttf"
  done
  echo "$2: $i files"
}
fetch 'Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800' bricolage
fetch 'Instrument+Sans:wght@400;500;600;700' instrument
fetch 'JetBrains+Mono:wght@400;500;700' jbmono
fc-cache -f >/dev/null 2>&1 || true
