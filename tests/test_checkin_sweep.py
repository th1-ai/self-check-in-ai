"""Tests for the pure sweep engine (tools/sweep.py). No I/O, no Store."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sweep import CheckinGuest, door_code_activation, door_code_for, run_sweep

WINDOWS = {"invite_window_days": 30, "chase_window_days": 5, "chase_cooldown_days": 2,
          "room_ready_hour": 14, "desk_minutes_saved": 6}
RULES_ALL_ON = {"invite-window-30": True, "follow-up-5": True}


def _guest(**kw) -> CheckinGuest:
    base = dict(res_ref="R1", guest_name="Test Guest", first_name="Test",
               arrival_offset=5, invite_status="not_sent", portal_step="none",
               invited_hours_ago=None)
    base.update(kw)
    return CheckinGuest(**base)


def test_invite_within_window_is_offered():
    result = run_sweep([_guest(arrival_offset=10)], rules=RULES_ALL_ON, windows=WINDOWS)
    assert len(result.invites) == 1
    assert result.invites[0].guest.res_ref == "R1"


def test_invite_outside_window_skips_with_reason():
    result = run_sweep([_guest(arrival_offset=38)], rules=RULES_ALL_ON, windows=WINDOWS)
    assert not result.actions
    assert "outside the 30-day window" in result.skips[0].reason


def test_invite_rule_off_skips_even_inside_window():
    rules = {**RULES_ALL_ON, "invite-window-30": False}
    result = run_sweep([_guest(arrival_offset=10)], rules=rules, windows=WINDOWS)
    assert not result.actions
    assert "off by rule" in result.skips[0].reason


def test_follow_up_fires_inside_chase_window_past_cooldown():
    guest = _guest(invite_status="invited", portal_step="started", arrival_offset=3,
                   invited_hours_ago=100)
    result = run_sweep([guest], rules=RULES_ALL_ON, windows=WINDOWS)
    assert len(result.follow_ups) == 1


def test_follow_up_outside_chase_window_skips():
    guest = _guest(invite_status="invited", arrival_offset=9, invited_hours_ago=200)
    result = run_sweep([guest], rules=RULES_ALL_ON, windows=WINDOWS)
    assert not result.actions
    assert "chasing starts at D-5" in result.skips[0].reason


def test_follow_up_within_cooldown_skips():
    guest = _guest(invite_status="invited", arrival_offset=2, invited_hours_ago=10)
    result = run_sweep([guest], rules=RULES_ALL_ON, windows=WINDOWS)
    assert not result.actions
    assert "one chase per two days" in result.skips[0].reason


def test_completed_guest_is_always_skipped():
    guest = _guest(invite_status="invited", portal_step="completed", arrival_offset=1)
    result = run_sweep([guest], rules=RULES_ALL_ON, windows=WINDOWS)
    assert not result.actions
    assert "already checked in online" in result.skips[0].reason


def test_sweep_is_sorted_by_arrival_offset():
    far = _guest(res_ref="FAR", arrival_offset=20)
    near = _guest(res_ref="NEAR", arrival_offset=1)
    result = run_sweep([far, near], rules=RULES_ALL_ON, windows=WINDOWS)
    assert [a.guest.res_ref for a in result.actions] == ["NEAR", "FAR"]


def test_door_code_for_is_deterministic_and_matches_the_known_value():
    # room 412 -> ((412*7919) % 9000) + 1000 = 5628
    assert door_code_for("412") == "5-6-2-8"
    assert door_code_for("412") == door_code_for(412)


def test_door_code_activation_before_and_after_ready_hour():
    assert door_code_activation(room_ready_hour=14, clock_hour=11) is False
    assert door_code_activation(room_ready_hour=14, clock_hour=15) is True
    assert door_code_activation(room_ready_hour=14, clock_hour=14) is True


def test_headline_when_nothing_to_do():
    result = run_sweep([], rules=RULES_ALL_ON, windows=WINDOWS)
    assert "Nothing to send this morning" in result.headline()


def test_headline_counts_actions_and_skips():
    guests = [_guest(res_ref="A", arrival_offset=1),
             _guest(res_ref="B", invite_status="invited", arrival_offset=100,
                    invited_hours_ago=1000)]
    result = run_sweep(guests, rules=RULES_ALL_ON, windows=WINDOWS)
    headline = result.headline()
    assert "1 invite" in headline and "1 stay" in headline
