"""Resources for one study block.

The load-bearing claim of this module is that a recommended link always
opens. A model asked for URLs returns ones that look right and 404, and a
student who taps two dead links in their first session stops tapping. So
the model picks a provider and writes a query, and the application builds
the URL -- which makes a hallucinated link structurally impossible rather
than merely unlikely.

Most of these tests are about that boundary: everything the model can get
wrong should cost a *missing* resource, never a broken one.
"""

from __future__ import annotations

import json
from urllib.parse import unquote_plus, urlparse

import pytest

import study_resources as sr


BLOCK = {
    "assignment": "Problem set 4: implicit differentiation",
    "course": "AP Calculus",
    "duration_minutes": 45,
    "notes": "",
    "description": "",
}


def chat_returning(payload):
    def _chat(messages):
        return payload if isinstance(payload, str) else json.dumps(payload)
    return _chat


# ── The model never writes a URL ─────────────────────────────────────


def test_a_provider_the_model_invented_yields_nothing(sr_=sr):
    """The failure this whole design exists to prevent. An unknown key is
    dropped rather than turned into a link that does not open."""
    assert sr.build_url("totally-made-up-site", "derivatives") is None


def test_an_invented_provider_drops_the_resource_not_the_batch():
    out = sr.parse_model_resources(json.dumps({"resources": [
        {"provider": "mathhelper2000", "query": "derivatives", "title": "A", "why": "x"},
        {"provider": "khan", "query": "implicit differentiation", "title": "B", "why": "y"},
    ]}))
    assert [r["title"] for r in out] == ["B"]


def test_every_returned_url_belongs_to_a_provider_we_control():
    out = sr.parse_model_resources(json.dumps({"resources": [
        {"provider": "khan", "query": "a", "title": "A", "why": ""},
        {"provider": "youtube", "query": "b", "title": "B", "why": ""},
        {"provider": "openstax", "query": "c", "title": "C", "why": ""},
    ]}))
    hosts = {urlparse(r["url"]).netloc for r in out}
    known = {urlparse(p.search_template).netloc for p in sr.PROVIDERS}
    assert hosts <= known


def test_a_url_the_model_supplies_directly_is_ignored():
    """Even if it puts one in the query field, it becomes search terms --
    it never becomes the destination."""
    out = sr.parse_model_resources(json.dumps({"resources": [
        {"provider": "khan", "query": "https://evil.test/malware",
         "title": "A", "why": ""},
    ]}))
    assert len(out) == 1
    assert urlparse(out[0]["url"]).netloc == "www.khanacademy.org"
    assert "evil.test" not in urlparse(out[0]["url"]).netloc


def test_the_query_is_url_encoded():
    url = sr.build_url("khan", "chain rule & implicit differentiation")
    assert " " not in url
    assert "chain rule & implicit differentiation" == unquote_plus(
        url.split("page_search_query=")[1])


def test_every_provider_template_has_a_query_slot():
    """A template without {q} would send every student to the same page."""
    for p in sr.PROVIDERS:
        assert "{q}" in p.search_template, p.key


def test_every_provider_template_is_https():
    for p in sr.PROVIDERS:
        assert p.search_template.startswith("https://"), p.key


def test_provider_keys_are_unique():
    keys = [p.key for p in sr.PROVIDERS]
    assert len(keys) == len(set(keys))


# ── Bad model output costs a missing resource, never a broken one ────


@pytest.mark.parametrize("raw", [
    "", "not json at all", "null", "[]", "{}", '{"resources": null}',
    '{"resources": "nope"}', '{"resources": [null, 3, "x"]}',
])
def test_unusable_output_yields_nothing_rather_than_raising(raw):
    assert sr.parse_model_resources(raw) == []


def test_json_wrapped_in_prose_is_still_read():
    """Models add a sentence before the JSON often enough to handle it."""
    out = sr.parse_model_resources(
        'Sure! Here you go:\n{"resources": [{"provider": "khan", '
        '"query": "derivatives", "title": "Derivatives", "why": "practice"}]}')
    assert len(out) == 1


def test_a_resource_with_no_query_is_dropped():
    assert sr.parse_model_resources(json.dumps({"resources": [
        {"provider": "khan", "query": "  ", "title": "A", "why": ""}]})) == []


def test_a_missing_title_falls_back_to_the_provider_name():
    out = sr.parse_model_resources(json.dumps({"resources": [
        {"provider": "khan", "query": "derivatives", "why": ""}]}))
    assert out[0]["title"] == "Khan Academy"


def test_duplicate_suggestions_are_collapsed():
    out = sr.parse_model_resources(json.dumps({"resources": [
        {"provider": "khan", "query": "derivatives", "title": "A", "why": ""},
        {"provider": "khan", "query": "derivatives", "title": "B", "why": ""},
    ]}))
    assert len(out) == 1


def test_the_list_is_capped():
    """A study block is 45 minutes. Twelve links is a reading list."""
    many = [{"provider": "khan", "query": f"topic {i}", "title": f"T{i}", "why": ""}
            for i in range(12)]
    assert len(sr.parse_model_resources(json.dumps({"resources": many}))) == sr.MAX_RESULTS


# ── The student's own material comes first ───────────────────────────


def test_links_in_the_assignment_are_surfaced():
    """A teacher's own link is the actual material and needs no model."""
    rows = sr.links_in_description(
        "Read https://example.edu/notes/ch4.pdf before Friday.")
    assert rows[0]["url"] == "https://example.edu/notes/ch4.pdf"
    assert rows[0]["source"] == "assignment"


def test_links_back_into_the_lms_are_not_offered_as_resources():
    """That is where the student already is."""
    assert sr.links_in_description(
        "See https://school.instructure.com/courses/1/assignments/2") == []


def test_trailing_punctuation_is_not_part_of_the_link():
    rows = sr.links_in_description("Use https://example.edu/a.pdf, then stop.")
    assert rows[0]["url"] == "https://example.edu/a.pdf"


def test_a_description_with_no_links_is_not_an_error():
    assert sr.links_in_description("Just read chapter four.") == []
    assert sr.links_in_description("") == []
    assert sr.links_in_description(None) == []


def test_the_students_own_links_outrank_suggestions():
    block = dict(BLOCK, description="Start with https://example.edu/ch4.pdf")
    out = sr.resources_for_block(block, chat=chat_returning({"resources": [
        {"provider": "khan", "query": "derivatives", "title": "Khan", "why": ""}]}))
    assert out[0]["source"] == "assignment"


# ── Linked accounts ──────────────────────────────────────────────────


class Acct:
    def __init__(self, provider, label, url, course=""):
        self.provider, self.label, self.url, self.course = provider, label, url, course


def test_a_linked_set_is_offered_for_its_own_course():
    out = sr.linked_account_resources(
        [Acct("quizlet", "Unit 4 vocab", "https://quizlet.com/123/x", "AP Calculus")],
        course="AP Calculus")
    assert out[0]["title"] == "Unit 4 vocab"
    assert out[0]["source"] == "linked_account"


def test_a_linked_set_for_another_course_is_not_offered():
    """A Biology deck during History is noise, and noise is what makes a
    student stop opening the panel."""
    assert sr.linked_account_resources(
        [Acct("quizlet", "Cells", "https://quizlet.com/1/x", "Biology")],
        course="AP Calculus") == []


def test_an_account_with_no_course_is_offered_everywhere():
    """Someone who linked their Khan profile without naming a course means
    it for all of them."""
    out = sr.linked_account_resources(
        [Acct("khan", "My Khan", "https://khanacademy.org/profile/me")],
        course="AP Calculus")
    assert len(out) == 1


def test_course_matching_ignores_case_and_padding():
    out = sr.linked_account_resources(
        [Acct("quizlet", "V", "https://quizlet.com/1/x", "  ap calculus ")],
        course="AP Calculus")
    assert len(out) == 1


def test_an_account_with_no_url_is_skipped():
    assert sr.linked_account_resources([Acct("quizlet", "Empty", "")]) == []


def test_linked_material_outranks_everything():
    block = dict(BLOCK, description="Also https://example.edu/ch4.pdf")
    out = sr.resources_for_block(
        block,
        accounts=[Acct("quizlet", "Unit 4", "https://quizlet.com/1/x", "AP Calculus")],
        chat=chat_returning({"resources": [
            {"provider": "khan", "query": "derivatives", "title": "K", "why": ""}]}))
    assert out[0]["source"] == "linked_account"


# ── Whole-block behaviour ────────────────────────────────────────────


def test_a_break_gets_no_resources():
    """A break is the one block where suggesting work is actively wrong."""
    assert sr.resources_for_block(
        {"assignment": "Break", "is_break": True},
        chat=chat_returning({"resources": [
            {"provider": "khan", "query": "x", "title": "K", "why": ""}]})) == []


def test_an_ai_outage_still_returns_the_students_own_links():
    """The panel going empty because a model is down loses the one source
    that never needed it."""
    def boom(messages):
        raise RuntimeError("quota exhausted")
    block = dict(BLOCK, description="Read https://example.edu/ch4.pdf")
    out = sr.resources_for_block(block, chat=boom)
    assert [r["url"] for r in out] == ["https://example.edu/ch4.pdf"]


def test_no_ai_configured_is_not_an_error():
    block = dict(BLOCK, description="Read https://example.edu/ch4.pdf")
    out = sr.resources_for_block(block, chat=None)
    assert len(out) == 1


def test_the_block_is_capped_across_all_sources():
    block = dict(BLOCK, description=" ".join(
        f"https://example.edu/{i}.pdf" for i in range(5)))
    out = sr.resources_for_block(block, accounts=[
        Acct("quizlet", f"S{i}", f"https://quizlet.com/{i}/x") for i in range(5)])
    assert len(out) == sr.MAX_RESULTS


def test_the_same_url_is_not_listed_twice_across_sources():
    block = dict(BLOCK, description="https://example.edu/ch4.pdf")
    out = sr.resources_for_block(block, accounts=[
        Acct("khan", "Mine", "https://example.edu/ch4.pdf")])
    assert len({r["url"] for r in out}) == len(out)


# ── The prompt ───────────────────────────────────────────────────────


def test_the_prompt_lists_only_providers_we_can_build_urls_for():
    text = sr.build_prompt(BLOCK)[0]["content"]
    for p in sr.PROVIDERS:
        assert p.key in text


def test_the_prompt_tells_the_model_not_to_write_urls():
    text = sr.build_prompt(BLOCK)[0]["content"].lower()
    assert "never write urls" in text or "never writes urls" in text


def test_the_prompt_carries_the_block_the_student_is_looking_at():
    user = sr.build_prompt(BLOCK)[1]["content"]
    assert "implicit differentiation" in user
    assert "AP Calculus" in user


def test_a_huge_description_is_truncated_before_it_reaches_the_model():
    block = dict(BLOCK, description="x" * 50000)
    assert len(sr.build_prompt(block)[1]["content"]) < 5000
