"""tools/sweep.py - the pre-arrival sweep engine. Pure function, no I/O.

Ported from the source `checkin-engine.ts` (`specs/self-check-in-ai.md`
section 3). Every window check and skip reason in the output traces back to
an input field - there is no model call anywhere in this file, and there
never should be. `tools/checkin.py` is the I/O shell around it: it loads
guests from the PMS and the agent's own tracking table, calls
:func:`run_sweep`, and turns the result into drafted, queued messages.

    from tools.sweep import CheckinGuest, run_sweep, door_code_for
    result = run_sweep(guests, rules={...}, windows={...})
    for action in result.actions:      # invite / follow_up, in order
        ...
    for skip in result.skips:          # every skip has a reason
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Defaults match the source engine (docs/how-it-works.md). config/agent.yaml's
# `windows:` block overrides these; tools/checkin.py always passes an explicit
# `windows` dict, so these are only the fallback for a bare call in a test.
DEFAULT_WINDOWS = {
    "invite_window_days": 30,
    "chase_window_days": 5,
    "chase_cooldown_days": 2,
    "room_ready_hour": 14,
    "desk_minutes_saved": 6,
}

# portal_step -> the phrase used in a follow-up's opening line.
STALL_LABELS = {
    "none": "not started",
    "started": "opened the portal but stopped at their details",
    "id": "stopped at the ID photo",
    "waiver": "stopped at the signature",
    "payment": "everything done except payment",
}


@dataclass
class CheckinGuest:
    """One reservation's check-in state, as the sweep needs to see it.

    `arrival_offset` and `invited_hours_ago` are computed by the caller
    (`tools/checkin.py`) relative to "today", so this dataclass itself has no
    notion of the wall clock - that keeps :func:`run_sweep` a pure function.
    """

    res_ref: str
    guest_name: str
    first_name: str
    arrival_offset: int
    invite_status: str          # not_sent | invited | reminded
    portal_step: str            # none | started | id | waiver | payment | completed
    invited_hours_ago: float | None = None

    @property
    def stall_label(self) -> str:
        return STALL_LABELS.get(self.portal_step, STALL_LABELS["none"])


@dataclass
class SweepAction:
    kind: str            # invite | follow_up
    guest: CheckinGuest
    reason: str


@dataclass
class SweepSkip:
    guest: CheckinGuest
    reason: str


@dataclass
class SweepResult:
    actions: list[SweepAction] = field(default_factory=list)
    skips: list[SweepSkip] = field(default_factory=list)

    @property
    def invites(self) -> list[SweepAction]:
        return [a for a in self.actions if a.kind == "invite"]

    @property
    def follow_ups(self) -> list[SweepAction]:
        return [a for a in self.actions if a.kind == "follow_up"]

    def headline(self) -> str:
        n_invite, n_follow, n_skip = len(self.invites), len(self.follow_ups), len(self.skips)
        if not n_invite and not n_follow:
            return ("Nothing to send this morning - every arrival already has its link, "
                    "its nudge or its completed check-in.")
        return (f"{n_invite} invite{'s' if n_invite != 1 else ''} sent · "
                f"{n_follow} follow-up{'s' if n_follow != 1 else ''} chased · "
                f"{n_skip} stay{'s' if n_skip != 1 else ''} deliberately left alone")


def _rule_on(rules: dict, key: str) -> bool:
    value = rules.get(key, True)
    if isinstance(value, dict):
        return bool(value.get("enabled", True))
    return bool(value)


def run_sweep(guests: list[CheckinGuest], *, rules: dict | None = None,
             windows: dict | None = None) -> SweepResult:
    """Decide invite / follow-up / skip for every guest. No I/O, no model call.

    `guests` should already be sorted or not - this function sorts by
    `arrival_offset` ascending itself, matching the source's step order.
    """
    rules = rules or {}
    w = {**DEFAULT_WINDOWS, **(windows or {})}
    result = SweepResult()

    for guest in sorted(guests, key=lambda g: g.arrival_offset):
        if guest.invite_status == "not_sent":
            if guest.portal_step == "completed":
                # Defensive: a completed guest should never still be not_sent,
                # but never lose a guest to a data inconsistency - treat as done.
                result.skips.append(SweepSkip(guest, "already checked in online"))
                continue
            if guest.arrival_offset > w["invite_window_days"]:
                result.skips.append(SweepSkip(
                    guest,
                    f"outside the {w['invite_window_days']}-day window (arrives in "
                    f"{guest.arrival_offset} days) - the invite goes out at "
                    f"D-{w['invite_window_days']}"))
                continue
            if not _rule_on(rules, "invite-window-30"):
                result.skips.append(SweepSkip(
                    guest,
                    "inside the window, but automatic invites are off by rule - the link "
                    "stays queued for a human"))
                continue
            result.actions.append(SweepAction(
                "invite", guest,
                f"arrives in {guest.arrival_offset} days, inside the "
                f"{w['invite_window_days']}-day invite window"))
            continue

        if guest.portal_step == "completed":
            result.skips.append(SweepSkip(
                guest, "already checked in online - nothing more to send"))
            continue

        # invited or reminded, not yet completed: the chase branch.
        if guest.arrival_offset > w["chase_window_days"]:
            result.skips.append(SweepSkip(
                guest,
                f"link is out but arrival is {guest.arrival_offset} days away - chasing "
                f"starts at D-{w['chase_window_days']}"))
            continue
        if not _rule_on(rules, "follow-up-5"):
            result.skips.append(SweepSkip(
                guest,
                "unfinished check-in inside the chase window, but follow-ups are off "
                "by rule"))
            continue
        cooldown_hours = w["chase_cooldown_days"] * 24
        if guest.invited_hours_ago is not None and guest.invited_hours_ago < cooldown_hours:
            result.skips.append(SweepSkip(
                guest,
                f"already nudged within the last {cooldown_hours:.0f} hours - one chase "
                "per two days, never more"))
            continue
        result.actions.append(SweepAction(
            "follow_up", guest,
            f"arrives in {guest.arrival_offset} days, {guest.stall_label}"))

    return result


def door_code_for(room_number: str | int) -> str:
    """Stable fake door code derived only from the room number - never random.

    `((room * 7919) mod 9000) + 1000`, formatted with hyphens for easy
    reading ("4129" -> "4-1-2-9"), matching the source exactly.
    """
    try:
        room = int(str(room_number).strip() or 0)
    except ValueError:
        room = sum(ord(c) for c in str(room_number))
    code = ((room * 7919) % 9000) + 1000
    digits = str(code)
    return "-".join(digits)


def door_code_activation(*, room_ready_hour: int, clock_hour: int) -> bool:
    """True once the room (and so the code) is "ready", per the clock hour."""
    return clock_hour >= room_ready_hour
