# Daily AI tips: carousel + video

A scheduled routine runs every morning and makes **one 10-slide carousel**
(1080×1350) and **one 36-second reel** (1080×1920, 60fps, original soundtrack)
on AI tips, plus a TikTok title and description. Both are built from one
researched, source-checked `content.json`.

| File | What it is |
| --- | --- |
| `BRIEF.md` | The job's instructions: theme rotation, the quality bar for a tip, verification, length limits, caption format. Edit this to steer the output. |
| `sample-content.json` | The shape of a day's content (and the 2026-09-23 run). |
| `reference.html` | The approved carousel design. Each run copies it and rewrites the content. |
| `render-carousel.js` | Carousel page → ten PNGs + `preview.jpg`. Fails on a missing font. |
| `reel.html` | The video template. Data-driven: `window.__setContent(content)` then `window.seek(t)`. |
| `render-video.js` | `content.json` → `reel.mp4` + `reel-sfx-only.mp4`, or `--probe` stills to check layout. |
| `synth.js` | The soundtrack and sound effects, synthesized on the video's fixed 120 BPM timeline. |
| `setup.sh` | Installs the three typefaces and a local ffmpeg. |
| `history.md` | Every day's theme and tips. The job appends to it and never repeats a tip. |

Run it by hand:

```bash
bash marketing/daily-content/setup.sh
node marketing/daily-content/render-carousel.js marketing/daily-content/reference.html /tmp/out
node marketing/daily-content/render-video.js marketing/daily-content/sample-content.json /tmp/out
```

This is marketing tooling, not app code. Nothing in the app imports it.
