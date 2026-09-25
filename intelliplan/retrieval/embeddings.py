"""Embedders for the retrieval index.

``lexical_embed`` — the feature-hashing trick over unigrams and bigrams with
sublinear term frequency, L2-normalised. Deterministic across processes
(crc32, not Python's salted ``hash``), needs no fitted vocabulary, and costs
nothing, so every passage always has one. It is what keeps retrieval working
with no API key, in tests, and when the embedding API is down.

``dense_embed`` — Gemini's embedding model. Understands that "mitosis" and
"cell division" are the same question, which lexical matching cannot. The
model ID is an env var because Gemini model IDs get retired and a stale one
must degrade to lexical, not break the feature.
"""

from __future__ import annotations

import logging
import math
import os
import re
import zlib
from collections import Counter

# Before numpy's first import: its bundled OpenBLAS otherwise starts one
# thread per *host* CPU. In a container that is dozens of threads per
# gunicorn worker for vector math that is a single small matmul.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import numpy as np  # noqa: E402

logger = logging.getLogger(__name__)

LEXICAL_DIM = 1024
DENSE_DIM = 768
DENSE_BATCH = 32

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_STOP = frozenset(
    "a an and are as at be but by for from has have i if in into is it its "
    "me my of on or our so than that the their them then there these they "
    "this to was we were what when which who will with you your do does did "
    "not no can could should would about just also".split()
)


def dense_model() -> str:
    return os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def _stem(tok: str) -> str:
    # Just enough folding that "equations"/"equation" and "solving"/"solve"
    # share a bucket. A real stemmer is not worth a dependency here.
    for suffix in ("ing", "es", "ed", "s"):
        if len(tok) > len(suffix) + 3 and tok.endswith(suffix):
            return tok[: -len(suffix)]
    return tok


def lexical_embed(text: str) -> np.ndarray:
    toks = [_stem(t) for t in _tokens(text)]
    feats = Counter(toks)
    feats.update(f"{a}_{b}" for a, b in zip(toks, toks[1:]))
    vec = np.zeros(LEXICAL_DIM, dtype=np.float32)
    for feat, tf in feats.items():
        h = zlib.crc32(feat.encode("utf-8"))
        sign = 1.0 if (h >> 31) & 1 else -1.0
        vec[h % LEXICAL_DIM] += sign * (1.0 + math.log(tf))
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


def dense_embed(texts: list[str], *, is_query: bool) -> list[np.ndarray] | None:
    """Batch-embed with Gemini. Returns None (never raises) when unavailable."""
    if not texts:
        return []
    try:
        from ai_provider import _gemini_client, gemini_api_key
        from google.genai import types

        if not gemini_api_key():
            return None
        client = _gemini_client()
        task = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
        out: list[np.ndarray] = []
        for i in range(0, len(texts), DENSE_BATCH):
            resp = client.models.embed_content(
                model=dense_model(),
                contents=texts[i : i + DENSE_BATCH],
                config=types.EmbedContentConfig(
                    task_type=task, output_dimensionality=DENSE_DIM
                ),
            )
            for emb in resp.embeddings:
                vec = np.asarray(emb.values, dtype=np.float32)
                # Truncated-dimension Gemini vectors are not unit length.
                norm = float(np.linalg.norm(vec))
                out.append(vec / norm if norm else vec)
        return out if len(out) == len(texts) else None
    except Exception as exc:  # network, quota, retired model ID
        logger.warning("Dense embedding unavailable, using lexical only: %s", exc)
        return None


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)
