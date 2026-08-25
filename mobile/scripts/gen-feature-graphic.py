#!/usr/bin/env python3
"""Generate the Play Store feature graphic (1024x500).

Run from mobile/:  python3 scripts/gen-feature-graphic.py

Google requires a feature graphic before a listing can be published, and a
missing one blocks submission outright. This produces a clean, on-brand
placeholder from assets already in the repo, so the listing is never held up
waiting on a design pass.

It is a placeholder in the honest sense: correct dimensions, correct brand
colours, no text that the store will crop or that duplicates the app name
badge Play draws over it. Replace it with a designed asset when there is
one — the file path is all the Play Console cares about.

Kept as a script rather than a checked-in image for the same reason
gen-icons.py is: change the source icon and re-run, and nothing drifts.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SRC_ICON = ROOT / "assets" / "icon.png"
OUT = ROOT / "assets" / "play-feature-graphic.png"

#: Exactly what Play asks for. Anything else is rejected at upload.
SIZE = (1024, 500)

#: The same indigo→violet ramp the app header and the emails use.
GRADIENT_FROM = (30, 27, 75)     # #1e1b4b
GRADIENT_MID = (49, 46, 129)     # #312e81
GRADIENT_TO = (76, 29, 149)      # #4c1d95

TITLE = "IntelliPlan"
TAGLINE = "Your assignments, planned."

#: The square of the source icon holding the symbol but not the wordmark,
#: as (left, top, right, bottom) fractions. Re-measure this if the icon art
#: is ever redrawn.
SYMBOL_CROP = (0.17, 0.12, 0.77, 0.72)

#: Inter is the product face but is not installed on most machines, so the
#: script degrades to whatever is available rather than failing. DejaVu ships
#: with Pillow's usual environments and with most Linux images.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
FONT_CANDIDATES_REGULAR = (
    "/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def _font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _gradient(size: tuple[int, int]) -> Image.Image:
    """A left-to-right three-stop ramp, drawn a column at a time.

    Diagonal would match the app more closely, but Play crops the graphic
    differently across surfaces and a horizontal ramp survives every crop.
    """
    width, height = size
    base = Image.new("RGB", size)
    draw = ImageDraw.Draw(base)
    for x in range(width):
        t = x / max(1, width - 1)
        if t < 0.5:
            local = t / 0.5
            start, end = GRADIENT_FROM, GRADIENT_MID
        else:
            local = (t - 0.5) / 0.5
            start, end = GRADIENT_MID, GRADIENT_TO
        colour = tuple(
            int(round(start[i] + (end[i] - start[i]) * local)) for i in range(3)
        )
        draw.line([(x, 0), (x, height)], fill=colour)
    return base


def main() -> None:
    canvas = _gradient(SIZE)
    draw = ImageDraw.Draw(canvas, "RGBA")

    # A couple of soft highlights so the panel is not a flat wash. Drawn on
    # their own layer and blurred: an un-blurred ellipse at this alpha reads
    # as a hard-edged circle someone left behind, not as light.
    glow = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for centre, radius, alpha in (((250, 90), 260, 30), ((880, 430), 300, 24)):
        glow_draw.ellipse(
            [
                centre[0] - radius,
                centre[1] - radius,
                centre[0] + radius,
                centre[1] + radius,
            ],
            fill=(255, 255, 255, alpha),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(110))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Icon on the left, generously inset. Play overlays UI near the edges on
    # some surfaces, so nothing meaningful goes within ~80px of one.
    icon_box = 240
    if SRC_ICON.exists():
        icon = Image.open(SRC_ICON).convert("RGBA")
        # Crop to the mark alone. The source icon carries the wordmark under
        # the symbol, and using it whole puts "IntelliPlan" on the graphic
        # twice — once clipped by the rounded corner, once as the title
        # beside it.
        width, height = icon.size
        icon = icon.crop(
            (
                int(width * SYMBOL_CROP[0]),
                int(height * SYMBOL_CROP[1]),
                int(width * SYMBOL_CROP[2]),
                int(height * SYMBOL_CROP[3]),
            )
        )
        icon = icon.resize((icon_box, icon_box), Image.LANCZOS)
        rounded = Image.new("L", (icon_box, icon_box), 0)
        ImageDraw.Draw(rounded).rounded_rectangle(
            [0, 0, icon_box, icon_box], radius=int(icon_box * 0.22), fill=255
        )
        canvas.paste(icon, (104, (SIZE[1] - icon_box) // 2), rounded)
    else:
        print(f"warning: {SRC_ICON} is missing; drawing text only")

    text_x = 104 + icon_box + 56
    title_font = _font(FONT_CANDIDATES, 76)
    tagline_font = _font(FONT_CANDIDATES_REGULAR, 32)

    title_h = draw.textbbox((0, 0), TITLE, font=title_font)[3]
    tagline_h = draw.textbbox((0, 0), TAGLINE, font=tagline_font)[3]
    block_h = title_h + 22 + tagline_h
    top = (SIZE[1] - block_h) // 2

    draw.text((text_x, top), TITLE, font=title_font, fill=(255, 255, 255))
    draw.text(
        (text_x, top + title_h + 22),
        TAGLINE,
        font=tagline_font,
        fill=(196, 181, 253),  # #c4b5fd, the same muted violet the emails use
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT.parent)} ({SIZE[0]}x{SIZE[1]})")


if __name__ == "__main__":
    main()
