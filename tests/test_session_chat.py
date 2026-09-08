"""The shared chat panel (static/js/session-chat.js), executed rather than grepped.

groups.html rebuilds its whole panel on every ``openGroup()``, so
``initSessionChat`` is called again with a fresh host element each time and
the previous mount is left detached. That shape produced four bugs at once:

  * The 8-second poll was never cleared, and the detached mount's timer is
    unreachable from the page — so every group a student opened added another
    poll that kept hitting that group's endpoint until the tab closed.
  * ``render()`` reassigned innerHTML on every poll and then unconditionally
    scrolled to the bottom. A student reading back through history was yanked
    to the newest message every 8 seconds, and any text selection in the log
    was destroyed just as often.
  * The poll ran while the tab was hidden.
  * Nothing guarded ``send()``, so a second Enter posted the message twice.

Plus a blocking ``window.alert`` on a failed save, which ip-core.js's header
rules out ("never window.alert") and which freezes the poll behind it.

These run the real file under Node with a small DOM stub
(tests/js/session_chat_harness.js) so the assertions are about behaviour, not
about the source text. Node is present on ubuntu-latest, which is what CI
runs; the tests skip where it is not.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "session_chat_harness.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs Node to execute the panel"
)


@pytest.fixture(scope="module")
def observed():
    proc = subprocess.run(
        ["node", str(HARNESS)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# ── Reading history while the room is talking ────────────────────────


def test_an_unchanged_poll_does_not_redraw_the_log(observed):
    """Redrawing identical content throws away the student's text selection."""
    assert observed["redrew_on_unchanged_poll"] is False


def test_an_unchanged_poll_does_not_move_the_scroll_position(observed):
    assert observed["moved_scroll_on_unchanged_poll"] is False


def test_a_new_message_does_redraw(observed):
    """The skip above must not turn into "never updates"."""
    assert observed["redrew_on_new_message"] is True


def test_a_student_reading_history_is_not_yanked_to_the_bottom(observed):
    assert observed["yanked_scrolled_up_reader"] is False


def test_a_student_at_the_bottom_still_follows_the_conversation(observed):
    """The inverse guard: not scrolling at all would be its own bug."""
    assert observed["followed_reader_at_bottom"] is True


# ── The poll ─────────────────────────────────────────────────────────


def test_reopening_a_group_replaces_the_poll_rather_than_adding_one(observed):
    assert observed["intervals_after_first_mount"] == 1
    assert observed["intervals_after_remount"] == 1


def test_reopening_a_group_does_not_stack_visibility_listeners(observed):
    assert observed["vis_listeners_after_first_mount"] == 1
    assert observed["vis_listeners_after_remount"] == 1


def test_a_hidden_tab_is_not_polled(observed):
    assert observed["polled_while_hidden"] is False


def test_a_visible_tab_is_polled(observed):
    assert observed["polled_while_visible"] is True


# ── Sending ──────────────────────────────────────────────────────────


def test_an_impatient_second_click_does_not_post_twice(observed):
    assert observed["posts_from_double_click"] == 1


# ── Failure reporting ────────────────────────────────────────────────


def test_a_failed_save_does_not_open_a_blocking_dialog(observed):
    assert observed["exercised_a_failing_save"] is True, "probe never fired"
    assert observed["used_blocking_alert"] is False


def test_a_failed_save_is_reported_through_the_shared_toast(observed):
    assert observed["reported_through_ip"] is True
