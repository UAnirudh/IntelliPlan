"""Split note text into retrieval passages.

A passage is the unit the model gets quoted, so it should read as a
self-contained thought: split on paragraph, then sentence boundaries, pack up
to ``target`` characters, and carry the last sentence into the next passage
so an idea that straddles a boundary is findable from either side.
"""

from __future__ import annotations

import re

TARGET_CHARS = 700
MAX_CHARS = 1100
MAX_CHUNKS_PER_NOTE = 40

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_WS = re.compile(r"[ \t]+")


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = _WS.sub(" ", para.replace("\n", " ")).strip()
        if not para:
            continue
        for sent in _SENTENCE_SPLIT.split(para):
            sent = sent.strip()
            # A single run-on "sentence" (bullet dumps, OCR) is hard-wrapped
            # so no passage blows past MAX_CHARS.
            while len(sent) > MAX_CHARS:
                cut = sent.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
                out.append(sent[:cut].strip())
                sent = sent[cut:].strip()
            if sent:
                out.append(sent)
    return out


def chunk_text(text: str, target: int = TARGET_CHARS) -> list[str]:
    sents = _sentences(text or "")
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for sent in sents:
        if buf and size + len(sent) > target:
            chunks.append(" ".join(buf))
            # One sentence of overlap, unless it alone is most of a passage.
            buf = [buf[-1]] if len(buf[-1]) < target // 2 else []
            size = sum(len(s) + 1 for s in buf)
        buf.append(sent)
        size += len(sent) + 1
        if len(chunks) >= MAX_CHUNKS_PER_NOTE:
            break
    if buf and len(chunks) < MAX_CHUNKS_PER_NOTE:
        chunks.append(" ".join(buf))
    return chunks
