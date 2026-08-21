#!/usr/bin/env python3
"""Regenerate mobile/assets/* from the website's PWA icon.

Run from mobile/:  python3 scripts/gen-icons.py

Kept as a script rather than checked-in-by-hand images so the phone app and
the website cannot drift apart on branding: change static/icons/icon-512.png
and re-run this.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "static" / "icons" / "icon-512.png"
OUT = ROOT / "assets"

BG = "#f5f4f1"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    src = Image.open(SRC).convert("RGBA")

    # 1024×1024 is what App Store Connect and Play Console both want, and
    # the store icon must be fully opaque: iOS rejects an alpha channel and
    # a transparent Android icon shows the launcher wallpaper through it.
    icon = src.resize((1024, 1024), Image.LANCZOS)
    flat = Image.new("RGB", (1024, 1024), BG)
    flat.paste(icon, (0, 0), icon)
    flat.save(OUT / "icon.png")

    # Android masks the adaptive foreground to a circle/squircle and crops
    # roughly the outer sixth on each side, so the mark is inset rather
    # than filling the box — otherwise its edges get shaved off.
    fg = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    inner = src.resize((620, 620), Image.LANCZOS)
    fg.paste(inner, (202, 202), inner)
    fg.save(OUT / "adaptive-icon.png")

    # Splash: transparent and centred — expo-splash-screen scales this
    # against the background colour rather than stretching it edge to edge.
    splash = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    mark = src.resize((420, 420), Image.LANCZOS)
    splash.paste(mark, (302, 302), mark)
    splash.save(OUT / "splash-icon.png")

    src.resize((48, 48), Image.LANCZOS).save(OUT / "favicon.png")

    # Android renders the notification icon as a silhouette from the alpha
    # channel alone, so colour in the source is discarded either way.
    notif = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    n = src.resize((96, 96), Image.LANCZOS)
    notif.paste(n, (0, 0), n)
    notif.save(OUT / "notification-icon.png")

    print(f"wrote 5 icons to {OUT}")


if __name__ == "__main__":
    main()
