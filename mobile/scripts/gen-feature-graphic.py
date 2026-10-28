#!/usr/bin/env python3
"""Generate IntelliPlan's flat 1024x500 Play Store feature graphic.

Run from mobile/: python scripts/gen-feature-graphic.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "play-feature-graphic.png"
SIZE = (1024, 500)

BG = "#F7F9FC"
WHITE = "#FFFFFF"
INK = "#14233B"
MUTED = "#66758A"
LINE = "#E4EAF2"
BLUE = "#2878F0"
BLUE_SOFT = "#EAF2FF"
GREEN = "#17835D"
GREEN_SOFT = "#E8F6EF"
VIOLET = "#7657C8"
VIOLET_SOFT = "#F1EDFB"

FONT_REGULAR = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
FONT_SEMIBOLD = (
    "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int,
            fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def main() -> None:
    canvas = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(canvas)

    # A compact brand cue and message stay clear of the Play badge and crop edges.
    rounded(draw, (72, 91, 126, 145), 16, BLUE)
    draw.line([(86, 118), (96, 128), (114, 106)], fill=WHITE, width=5, joint="curve")
    draw.text((148, 96), "A clearer plan", font=font(FONT_SEMIBOLD, 42), fill=INK)
    draw.text((148, 150), "for every study day.", font=font(FONT_SEMIBOLD, 42), fill=BLUE)
    draw.text((74, 231), "Turn schoolwork into small, doable steps.",
              font=font(FONT_REGULAR, 23), fill=MUTED)

    # Three restrained proof points describe the product without making claims
    # about grades or guaranteed outcomes.
    features = [("Priorities", BLUE), ("Study plan", GREEN), ("Focus time", VIOLET)]
    x = 74
    for label, color in features:
        draw.ellipse((x, 310, x + 14, 324), fill=color)
        draw.text((x + 24, 304), label, font=font(FONT_SEMIBOLD, 18), fill=INK)
        x += 157

    # Small, illustrative Today timeline inspired by the real app's clean cards.
    # Keep the example labels generic and the UI free of gradients or tiny copy.
    rounded(draw, (552, 43, 952, 457), 28, WHITE, LINE, 2)
    draw.text((585, 76), "TODAY", font=font(FONT_SEMIBOLD, 15), fill=MUTED)
    draw.text((585, 104), "A good place to start", font=font(FONT_SEMIBOLD, 25), fill=INK)
    rounded(draw, (850, 76, 920, 105), 15, BLUE_SOFT)
    draw.text((862, 83), "3 steps", font=font(FONT_SEMIBOLD, 13), fill=BLUE)

    # Timeline rail
    draw.line([(606, 178), (606, 378)], fill=LINE, width=3)

    rows = [
        (166, "10:00", "Biology review", "Read the next chapter", BLUE_SOFT, BLUE, True),
        (255, "11:30", "Study block", "Practice a few problems", GREEN_SOFT, GREEN, False),
        (344, "1:00", "Take a break", "Then come back refreshed", VIOLET_SOFT, VIOLET, False),
    ]
    for y, time, title, detail, tint, accent, active in rows:
        draw.ellipse((598, y + 13, 614, y + 29), fill=accent if active else "#C7D0DD")
        draw.text((632, y + 4), time, font=font(FONT_SEMIBOLD, 15), fill=accent if active else MUTED)
        rounded(draw, (704, y - 3, 922, y + 70), 18, tint)
        draw.text((724, y + 7), title, font=font(FONT_SEMIBOLD, 17), fill=INK)
        draw.text((724, y + 36), detail, font=font(FONT_REGULAR, 13), fill=MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT.parent)} ({SIZE[0]}x{SIZE[1]})")


if __name__ == "__main__":
    main()
