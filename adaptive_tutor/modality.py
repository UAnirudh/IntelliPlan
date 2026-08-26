"""Learning-modality routing.

Ported from the adaptive-ai-tutor ``src/lib/tutor/modality.ts``. The tutor
detects how a student learns best (auditory / visual / reading) and routes
the response through the matching channels: voice narration, interactive
artifacts, or plain text.
"""

from __future__ import annotations

from typing import Literal, TypedDict

Modality = Literal["auditory", "visual", "reading"]
ModalityMode = Literal["auto", "auditory", "visual", "reading", "blended"]

MODALITY_MODES: tuple[str, ...] = ("auto", "auditory", "visual", "reading", "blended")


class ModalityWeights(TypedDict):
    auditory: float
    visual: float
    reading: float


class ActiveModalities(TypedDict):
    use_voice: bool
    use_artifacts: bool
    use_text: bool


DEFAULT_WEIGHTS: ModalityWeights = {"auditory": 0.33, "visual": 0.33, "reading": 0.34}

#: A modality only wins outright once it clears this share of the signal.
_DOMINANCE_FLOOR = 0.4
#: Below this gap between the top two modalities the learner reads as blended.
_BLEND_GAP = 0.15
#: Visual artifacts stay on for anyone with at least this much visual signal.
_ARTIFACT_FLOOR = 0.35


def normalize_weights(raw: object) -> ModalityWeights:
    """Coerce stored JSON into a valid weight triple that sums to 1.0."""
    if not isinstance(raw, dict):
        return dict(DEFAULT_WEIGHTS)  # type: ignore[return-value]

    weights = {}
    for key in ("auditory", "visual", "reading"):
        try:
            weights[key] = max(0.0, float(raw.get(key, 0.0)))
        except (TypeError, ValueError):
            weights[key] = 0.0

    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)  # type: ignore[return-value]

    return {key: value / total for key, value in weights.items()}  # type: ignore[return-value]


def get_dominant_modality(weights: ModalityWeights) -> Modality:
    if weights["auditory"] >= weights["visual"] and weights["auditory"] >= weights["reading"]:
        return "auditory"
    if weights["visual"] >= weights["reading"]:
        return "visual"
    return "reading"


def is_blended(weights: ModalityWeights) -> bool:
    ordered = sorted(weights.values(), reverse=True)
    return (ordered[0] - ordered[1]) < _BLEND_GAP


def blend_weights(previous: object, incoming: ModalityWeights) -> ModalityWeights:
    """Weighted moving average — 60% history, 40% the newest reading."""
    incoming = normalize_weights(incoming)
    if not isinstance(previous, dict):
        return incoming

    prior = normalize_weights(previous)
    return normalize_weights(
        {key: prior[key] * 0.6 + incoming[key] * 0.4 for key in prior}
    )


def get_active_modalities(mode: str, weights: ModalityWeights) -> ActiveModalities:
    """Decide which output channels this turn uses."""
    if mode == "auditory":
        return {"use_voice": True, "use_artifacts": True, "use_text": True}
    if mode == "visual":
        return {"use_voice": False, "use_artifacts": True, "use_text": True}
    if mode == "reading":
        return {"use_voice": False, "use_artifacts": False, "use_text": True}
    if mode == "blended":
        return {"use_voice": True, "use_artifacts": True, "use_text": True}

    # auto — follow the detected weights, but require a clear signal for voice.
    weights = normalize_weights(weights)
    dominant = get_dominant_modality(weights)

    if is_blended(weights):
        return {
            "use_voice": weights["auditory"] > _DOMINANCE_FLOOR,
            "use_artifacts": True,
            "use_text": True,
        }

    return {
        "use_voice": dominant == "auditory" and weights["auditory"] > _DOMINANCE_FLOOR,
        "use_artifacts": dominant == "visual" or weights["visual"] > _ARTIFACT_FLOOR,
        "use_text": True,
    }


MODALITY_LABELS: dict[str, dict[str, str]] = {
    "auto":     {"label": "Auto",     "description": "Plani detects your best learning style", "icon": "✨"},
    "auditory": {"label": "Auditory", "description": "Spoken explanations read aloud",         "icon": "🎧"},
    "visual":   {"label": "Visual",   "description": "Interactive diagrams and quizzes",       "icon": "📊"},
    "reading":  {"label": "Reading",  "description": "Text-based explanations",                "icon": "📖"},
    "blended":  {"label": "Blended",  "description": "All modes combined",                     "icon": "🔀"},
}
