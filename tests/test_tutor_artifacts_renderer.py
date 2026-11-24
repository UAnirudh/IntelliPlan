"""Static contracts for the declarative tutor artifact renderer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'static' / 'js' / 'tutor-artifacts.js'

ADVERTISED_TYPES = (
    'graph', 'geometry', 'chart', 'numberline', 'unit-circle', 'solid3d',
    'diagram', 'timeline', 'pacing', 'quiz', 'flashcards', 'svg-exercise',
)


def test_renderer_supports_every_advertised_type():
    source = ARTIFACTS.read_text(encoding='utf-8')
    for name in ADVERTISED_TYPES:
        assert name in source, name


def test_renderer_never_executes_model_markup():
    source = ARTIFACTS.read_text(encoding='utf-8')
    assert 'innerHTML =' not in source
    assert 'insertAdjacentHTML' not in source
    assert 'eval(' not in source
    assert 'new Function' not in source
    assert 'textContent' in source


def test_renderer_does_not_emit_model_supplied_urls_or_scripts():
    source = ARTIFACTS.read_text(encoding='utf-8').lower()
    assert 'javascript:' not in source
    assert '<script' not in source


def test_vision_prompt_allows_latex_and_a_geometry_artifact():
    source = (ROOT / 'chatbot_api.py').read_text(encoding='utf-8')
    assert 'Use LaTeX' in source
    assert ':::artifact{type=\\"geometry\\"}' in source
