"""Pollinations media generation — images, video and 3D.

Study material is often visual: a diagram of a cell, a labelled circuit, a
short clip of an orbit. None of the chat providers in ai_provider generate
media, so this is a separate client against https://gen.pollinations.ai.

Everything here is server-side. The key is a secret ``sk_`` key held in the
environment and never handed to the page -- a publishable key in browser code
would let anyone on the internet spend the balance, which is exactly the hole
ai_firewall exists to close everywhere else.

Defaults are the models chosen for IntelliPlan: flux for images, Seedance 2.0
Fast for video, Trellis 2 for 3D. Trellis ignores text entirely and works from
an image URL, so its helper takes one.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://gen.pollinations.ai").rstrip("/")

IMAGE_MODEL = os.getenv("POLLINATIONS_IMAGE_MODEL", "flux")
VIDEO_MODEL = os.getenv("POLLINATIONS_VIDEO_MODEL", "seedance-2.0-fast")
THREED_MODEL = os.getenv("POLLINATIONS_3D_MODEL", "trellis-2")

#: Generation is slow by nature: a video is tens of seconds of real work.
IMAGE_TIMEOUT = int(os.getenv("POLLINATIONS_IMAGE_TIMEOUT", "90"))
VIDEO_TIMEOUT = int(os.getenv("POLLINATIONS_VIDEO_TIMEOUT", "300"))
THREED_TIMEOUT = int(os.getenv("POLLINATIONS_3D_TIMEOUT", "300"))

#: Anything larger is a bug or an attack, not a diagram.
MAX_BYTES = int(os.getenv("POLLINATIONS_MAX_BYTES", str(20 * 1024 * 1024)))


class PollinationsUnavailable(RuntimeError):
    """No key configured, or the service could not be reached."""


class PollinationsError(RuntimeError):
    """The service answered, and the answer was an error."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_key() -> str | None:
    return os.getenv("POLLINATIONS_API_KEY")


def available() -> bool:
    return bool(api_key())


def _get(path: str, params: dict[str, str], timeout: int) -> tuple[bytes, str]:
    """GET a generation endpoint and return ``(bytes, content_type)``.

    The key travels in the Authorization header rather than the query string:
    a key in a URL ends up in access logs, proxy caches and referrer headers.
    """
    key = api_key()
    if not key:
        raise PollinationsUnavailable(
            "Media generation is not configured. Set POLLINATIONS_API_KEY "
            "(get one at https://enter.pollinations.ai/keys)."
        )
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise PollinationsError("Generated media is larger than the size ceiling.")
            return data, resp.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(600).decode("utf-8", "replace")
        except Exception:
            pass
        # 402 is the one worth naming: the balance is out, not the code broken.
        if exc.code == 402:
            raise PollinationsError("The media generation balance is empty.", 402) from exc
        raise PollinationsError(f"Pollinations returned {exc.code}: {body}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise PollinationsUnavailable(f"Could not reach Pollinations: {exc.reason}") from exc


def generate_image(prompt: str, *, model: str | None = None, width: int | None = None,
                   height: int | None = None, seed: int | None = None,
                   nologo: bool = True) -> tuple[bytes, str]:
    """A still image for a prompt. Returns ``(bytes, content_type)``."""
    clean = (prompt or "").strip()
    if not clean:
        raise ValueError("An image needs a prompt.")
    return _get(
        "/image/" + urllib.parse.quote(clean[:1000], safe=""),
        {
            "model": model or IMAGE_MODEL,
            "width": str(width) if width else "",
            "height": str(height) if height else "",
            "seed": str(seed) if seed is not None else "",
            "nologo": "true" if nologo else "",
        },
        IMAGE_TIMEOUT,
    )


def generate_video(prompt: str, *, model: str | None = None,
                   duration: int = 4, image_url: str | None = None) -> tuple[bytes, str]:
    """A short clip for a prompt. Returns ``(bytes, content_type)``.

    Duration is capped at eight seconds: video is billed per second and a
    study aid that runs longer than a worked example is not a study aid.
    """
    clean = (prompt or "").strip()
    if not clean:
        raise ValueError("A video needs a prompt.")
    return _get(
        "/video/" + urllib.parse.quote(clean[:1000], safe=""),
        {
            "model": model or VIDEO_MODEL,
            "duration": str(max(1, min(int(duration or 4), 8))),
            "image": image_url or "",
        },
        VIDEO_TIMEOUT,
    )


def generate_3d(image_url: str, *, model: str | None = None,
                resolution: str = "low") -> tuple[bytes, str]:
    """A GLB mesh from an image.

    Trellis 2 ignores any text prompt, so the path segment is a placeholder
    and the image URL carries the whole request. Resolution defaults to low:
    it is the fastest and cheapest, and a molecule a student spins on a phone
    does not need more.
    """
    if not (image_url or "").strip():
        raise ValueError("A 3D model needs an image URL.")
    if resolution not in ("low", "medium", "high"):
        resolution = "low"
    return _get(
        "/3d/no_prompt_for_trellis_needed",
        {
            "model": model or THREED_MODEL,
            "image": image_url.strip(),
            "resolution": resolution,
        },
        THREED_TIMEOUT,
    )
