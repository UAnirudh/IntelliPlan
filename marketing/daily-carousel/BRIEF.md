# Daily AI-tips carousel — the brief

The scheduled job reads this file every morning and follows it. Change
this file to change what the job makes.

## The job, in order

1. `bash marketing/daily-carousel/setup.sh` (installs the fonts).
2. Pick today's theme: the first theme in **Themes** below that does not
   appear in the last 30 entries of `history.md`, starting from the row
   `day-of-year mod 30` and moving down (wrap around).
3. Research. Use web search to find **7 tips** for that theme that clear
   the bar below. Read every tip already listed in `history.md` first;
   never repeat one, even reworded.
4. Verify. Every factual claim, number or "research shows" on a slide must
   be checked that day against a primary source (the lab's paper or blog,
   the vendor's own docs, a peer-reviewed study). If it cannot be verified,
   drop the tip and find another. Never invent or round up a number.
5. Build. Copy `reference.html` to a scratch folder, and rewrite the
   copy's slide content, keeping its CSS, components, thread and layout.
   Don't edit `reference.html` itself.
6. Render: `node marketing/daily-carousel/render.js <copy>.html <out-dir>`.
7. Look at `preview.jpg`, then open every slide whose layout changed.
   Fix clipped text, overlaps, text touching the footer, or a
   headline running past 2 lines, then re-render. Repeat until clean.
8. Write `caption.md` in the out dir (format below).
9. Deliver with SendUserFile: `preview.jpg` (display: render), the ten
   `slide-NN.png` (display: attach), and `caption.md`. Then repeat the TikTok
   title + description in the final chat message so it can be copied.
10. Append today's entry to `history.md`, then commit **only that file** with
    the message `chore(marketing): carousel log YYYY-MM-DD [skip ci]` and push
    to `claude/design-system-extraction-el4gea`. Never commit the PNGs.

## The bar for a tip

The audience is students and everyday AI users. A tip earns its slide only
if it is all three:

- **Non-obvious.** Most people don't know it. "Be specific" and "give context"
  are already done; skip anything at that level.
- **Understandable.** The WHY explains the mechanism in plain words a
  15-year-old follows. No jargon without a one-line definition.
- **Useful today.** It ends in a move the reader can make in the next chat:
  a copy-paste prompt, a setting to change, or a habit.

Good examples, already used (see `history.md`): long chats degrade
answers (Chroma, 2025); models lean toward agreeing with the user
(Anthropic, 2023); document first, question last (Anthropic docs).

No medical, legal or financial advice. Never present "top 1%" or similar
as a measured fact. Avoid brand-specific claims that may go stale
(button names, prices); when naming a feature, name what it's called
across ChatGPT, Claude and Gemini.

## Slide structure (10 slides, 1080×1350)

| Slide | Content |
| --- | --- |
| 1 | Hook, on the dark slide: the single most surprising tip as a claim, at most 8 words, with one key word highlighted. Under it: “+ 6 more things AI power users know that most people don’t.” (or a sentence in that voice that fits the theme), then the Swipe pill. The chat bubbles top right become three short, relatable lines on the theme. |
| 2–8 | One tip each: `01/07` numeral; a headline that fits 2 lines (about 28 characters); a `WHY` line of 150 characters or fewer, with the mechanism and the source in bold; a visual; a `✓` card with a prompt to copy, often with a `✕` card before it; the source in the footer or `.src` line. |
| 9 | Cheat sheet: the 7 tips as one-line habits. |
| 10 | CTA, dark: “Save this.”-style line with a highlight, one reason to save, and one funny send-to-a-friend line tied back to a tip. |

**Vary the visuals.** Don't make seven slides of bubble pairs. The
reference has a message strip, letter tiles against token chips, a
Fast/Thinking toggle, two-column lists, document stacks, a stat line
(“up to 30%”) and a stacked card library. Reuse and adapt them so
each slide shows its tip's mechanism.

Keep the series footer text `BETTER PROMPTS` until the owner provides
their handle; if `HANDLE` below is set, use it instead.

HANDLE:

## caption.md format

```
# TikTok
Title options (one per line, under 90 characters; lead with the hook):
1.
2.
3.

Description:
<one-line setup>
1️⃣ … 7️⃣  (one line per tip)
<source line> Save this for … 📌 + a send-to-a-friend line
#ai #chatgpt #aitips + 5–7 relevant tags

# Instagram
<2–3 line caption> + 5 hashtags
```

## Themes (rotation)

1. Prompting habits power users have
2. Studying and exam prep with AI
3. Writing with AI without sounding like AI
4. Research and fact-checking with AI
5. Features most people never turn on (memory, custom instructions, projects)
6. Privacy and safety: what not to paste into a chatbot
7. Coding with AI as a beginner
8. Image generation prompting
9. College and job applications with AI, done honestly
10. Voice mode and talking to AI
11. Learning a language with AI
12. Math and science with AI (and where it breaks)
13. Planning your week and beating procrastination with AI
14. Spotting hallucinations
15. Turning notes, PDFs and lectures into study material
16. Presentations and slides with AI
17. Email and messages that get replies
18. Using AI to make better decisions
19. AI myths, busted
20. Reading long documents with AI
21. Learning science and AI (retrieval practice, spacing)
22. AI for creators: hooks, scripts, captions
23. AI agents, explained simply
24. How chatbots actually work, in plain words
25. Getting honest feedback on your work
26. Brainstorming that doesn't come out generic
27. Spreadsheets and data with AI
28. Interview prep with AI
29. Using AI for school without cheating
30. Free AI tools and tricks most people miss
