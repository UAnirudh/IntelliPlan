from pathlib import Path


def test_empty_out_of_range_page_has_truthful_status_and_keeps_previous_available():
    source = Path("static/js/owner_schools.js").read_text(encoding="utf-8")
    template = Path("Main_Project/templates/owner_schools.html").read_text(encoding="utf-8")
    assert "state.schools.length" in source
    assert "No institutions on this page · ${state.total} total" in source
    assert "$('schoolsPrevious').disabled = state.offset <= 0" in source
    assert '<nav class="owner-page-controls" id="schoolPagination"' in template
