"""Regression tests for assignment-completion persistence.

The "it resets when I revisit the page" bug came from comparing raw
display-string titles. These tests pin the normalized-key behavior so a
completed assignment stays completed across realistic title drift.
"""

import App


def test_norm_title_collapses_entity_drift():
    # An LMS feed may render "Lab 3 & Writeup" but store "&amp;".
    assert App._norm_title("Lab 3 &amp; Writeup") == App._norm_title("Lab 3 & Writeup")


def test_norm_title_collapses_whitespace_and_case():
    assert App._norm_title("  Essay   Draft ") == App._norm_title("essay draft")


def test_norm_title_collapses_nbsp_and_smart_quotes():
    # NBSP (\xa0) and smart quotes are common in copied/synced titles.
    assert App._norm_title("Newton’s\xa0Laws") == App._norm_title("Newton's Laws")


def test_norm_title_empty_is_stable():
    assert App._norm_title("") == ""
    assert App._norm_title(None) == ""


def test_dismissed_set_matches_across_drift():
    d = App._DismissedSet(["Lab 3 &amp; Writeup", "  Essay Draft "])
    # Stored with entities/whitespace; queried with the clean display form.
    assert "Lab 3 & Writeup" in d
    assert "essay draft" in d
    assert "Unrelated Homework" not in d


def test_dismissed_set_is_falsy_when_empty():
    assert not App._DismissedSet([])
    assert len(App._DismissedSet([])) == 0


def test_dismissed_set_distinct_titles_dedupe_by_key():
    # Two drifted spellings of the same assignment collapse to one key.
    d = App._DismissedSet(["Quiz &amp; Review", "quiz & review"])
    assert len(d) == 1
