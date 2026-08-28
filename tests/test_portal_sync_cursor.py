"""Regression test for SIMULATION.md finding 1.

`tools/portal_sync.py:_real_events()` used to advance the
`portal_sync:file_offset` cursor to end-of-file as soon as it READ
`checkin_portal_events.jsonl`, before a single event in that batch had been
applied. If `apply_event()` raised `LLMPendingInteractive` partway through
(any door-code draft needing the `interactive` provider), `one_pass()`
returned exit 3 with the cursor already past every event in the read - so
the next `--once` silently dropped everything after the parked event: not
re-read, not logged, not escalated. Reproduced live in SIMULATION.md: 11
events in, a pause on event 5, 7 events lost.

This test writes the same 11-event shape, forces the pause at event 5 with
the `interactive` provider, answers the prompt exactly as a hotel's Claude
session would, and checks the second `--once` picks up EVERY remaining
event - including the declined-card and ID-mismatch escalations - not just
the door-code item that pended.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.config import load_settings, sub_data_dir
from core.store import Store

import portal_sync
import store_ext

TODAY = "2026-09-01"
EVENTS_PATH = "data/imports/checkin_portal_events.jsonl"


def _seed(store, res_ref, **kw) -> None:
    pms_fields = {
        "guest_name": f"Guest {res_ref}", "first_name": "Guest", "email": f"{res_ref}@example.com",
        "phone": "", "language": "en", "party": 1, "room_type": "Classic Double",
        "room_number": "301", "nights": 2, "check_in": TODAY, "check_out": "2026-09-03",
        "channel": "Direct", "vip": False, "smart_lock": False, "payment_kind": "balance_due",
        "balance": 100.0, "city_tax": 2.0, "currency": "EUR",
    }
    seed = {}
    for key, value in kw.items():
        (pms_fields if key in pms_fields else seed)[key] = value
    store_ext.sync_guest(store, res_ref, pms_fields, seed=seed)


def _write_events(lines: list[dict]) -> None:
    path = Path(os.environ["AGENT_REPO_ROOT"]) / EVENTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")


def _answer_pending_doorcode_prompt() -> None:
    pending = sub_data_dir("pending")
    prompts = list(pending.glob("doorcode-*.prompt.md"))
    assert len(prompts) == 1, f"expected exactly one pending doorcode prompt, got {prompts}"
    stem = prompts[0].name[: -len(".prompt.md")]
    (pending / f"{stem}.answer.json").write_text(json.dumps({
        "subject": "Your door code", "body": "Room 501, code ready from 3pm. The team.",
    }), encoding="utf-8")


def test_pause_mid_batch_then_resume_processes_every_event(tmp_path):
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / "t.db")
    store_ext.ensure_schema(store)

    _seed(store, "R1")
    _seed(store, "R2")
    _seed(store, "R3", id_status="verified", waiver_signed=1, portal_step="payment",
         smart_lock=True, room_number="501", payment_kind="prepaid", balance=0.0, city_tax=0.0)
    _seed(store, "R4")

    events = [
        {"event_id": "e01", "res_ref": "R1", "kind": "id_check", "id_match": True,
         "id_name": "Guest R1"},
        {"event_id": "e02", "res_ref": "R1", "kind": "waiver", "signed": True},
        {"event_id": "e03", "res_ref": "R2", "kind": "id_check", "id_match": True,
         "id_name": "Guest R2"},
        {"event_id": "e04", "res_ref": "R2", "kind": "waiver", "signed": True},
        # event 5: the door-code draft this pends on (interactive, no answer yet).
        {"event_id": "e05", "res_ref": "R3", "kind": "payment", "result": "authorized",
         "amount": 0},
        {"event_id": "e06", "res_ref": "R1", "kind": "payment", "result": "declined"},
        {"event_id": "e07", "res_ref": "R2", "kind": "payment", "result": "charged",
         "amount": 100},
        {"event_id": "e08", "res_ref": "R4", "kind": "id_check", "id_match": False,
         "id_name": "Wrong Name"},
        {"event_id": "e09", "res_ref": "R4", "kind": "waiver", "signed": False},
        {"event_id": "e10", "res_ref": "R2", "kind": "upsell", "slug": "wine-in-room",
         "confirmed": True, "price": 25},
        {"event_id": "e11", "res_ref": "R1", "kind": "upsell", "slug": "late-checkout",
         "confirmed": True, "price": 15},
    ]
    assert len(events) == 11
    _write_events(events)

    full_text = (Path(os.environ["AGENT_REPO_ROOT"]) / EVENTS_PATH).read_text(encoding="utf-8")

    # -- pass 1: pauses on event 5 -----------------------------------------
    code1, stats1 = portal_sync.one_pass(settings, store, provider="interactive", today=TODAY,
                                         demo=False, reset_cursor=False)
    assert code1 == 3
    assert stats1["processed"] == 4, "events 1-4 must have been fully applied before the pause"
    cursor_after_pause = store.get_cursor(portal_sync.CURSOR_KEY, 0)
    assert 0 < cursor_after_pause < len(full_text), (
        "cursor must stop at the last event actually applied, not jump to end-of-file")

    # -- answer the parked prompt, exactly like a hotel's Claude session ----
    _answer_pending_doorcode_prompt()

    # -- pass 2: must resume at event 5, not skip past it -------------------
    code2, stats2 = portal_sync.one_pass(settings, store, provider="interactive", today=TODAY,
                                         demo=False, reset_cursor=False)
    assert code2 == 0
    assert stats2["processed"] == 7, "events 5-11 must all be picked up on the resume"
    assert stats1["processed"] + stats2["processed"] == 11, "no event may be dropped"
    assert store.get_cursor(portal_sync.CURSOR_KEY, 0) == len(full_text)

    # -- nothing was silently lost: every event_id was actually applied -----
    for event in events:
        assert store.get("portal_sync:seen:" + event["event_id"]), (
            f"{event['event_id']} was never applied")

    # -- the specific cases finding 1 called out never vanish ---------------
    r1_escalations = [i for i in store.list_items(kind="checkin_escalation", limit=50)
                      if i.payload.get("res_ref") == "R1"]
    assert any("declined" in i.payload.get("reason", "") for i in r1_escalations)
    r4_escalations = [i for i in store.list_items(kind="checkin_escalation", limit=50)
                      if i.payload.get("res_ref") == "R4"]
    assert any("mismatch" in i.payload.get("reason", "") for i in r4_escalations)
    assert any("waiver" in i.payload.get("reason", "") for i in r4_escalations)
    assert all(i.review_status == "needs_human" for i in r1_escalations + r4_escalations)

    # -- the door-code item that pended actually resolved, not stuck at new -
    doorcode_item = store.get_by_external("checkin", "R3:doorcode-notice:" + TODAY)
    assert doorcode_item is not None
    assert doorcode_item.review_status == "pending_review"
    assert doorcode_item.draft and doorcode_item.draft.get("body")
    assert store_ext.get_guest(store, "R3")["door_code"]

    upsell_items = store.list_items(kind="checkin_upsell", limit=50)
    assert len(upsell_items) == 2
    assert all(i.review_status == "pending_review" for i in upsell_items)
    store.close()
