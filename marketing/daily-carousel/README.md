# Daily AI-tips carousel

A scheduled routine runs every morning and produces a new 10-slide
Instagram / TikTok carousel on AI tips, plus a TikTok title and description.

| File | What it is |
| --- | --- |
| `BRIEF.md` | The job's instructions: theme rotation, the quality bar for a tip, slide structure, caption format. Edit this to steer the output. |
| `reference.html` | The approved design (the 2026-09-23 carousel). Each run copies it and rewrites the content, so every day matches this look. |
| `render.js` | Renders a carousel page to ten 1080×1350 PNGs and a preview strip. Fails if a font is missing rather than falling back silently. |
| `setup.sh` | Installs Bricolage Grotesque, Instrument Sans and JetBrains Mono. |
| `history.md` | Every carousel made so far. The job appends to it and never repeats a tip. |

Run it by hand:

```bash
bash marketing/daily-carousel/setup.sh
node marketing/daily-carousel/render.js marketing/daily-carousel/reference.html /tmp/carousel-out
```

This is marketing tooling, not app code. Nothing in the app imports it.
