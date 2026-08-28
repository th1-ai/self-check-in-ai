"""Regression test for SIMULATION.md finding 2.

`tools/checkin.py:maybe_issue_door_code()` used to write `door_code` to
`checkin_guests` (the "already issued" gate it checks itself) BEFORE calling
the LLM to draft the guest notice. If that draft call paused on
`LLMPendingInteractive` (`llm.provider: interactive`), a retry saw the code
already set and short-circuited straight to `{"already_issued": True}` -
so the notice was never drafted and the `checkin_doorcode` item sat at
`review_status: new` with an empty draft forever, invisible to
`make review`.

This test drives the real pend -> answer -> retry cycle and checks: nothing
is persisted before the draft resolves, the pending code is cached on the
item's own payload under a `_`-prefixed key, and the retry actually drafts
instead of short-circuiting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.config import load_settings, sub_data_dir
from core.llm import LLMPendingInteractive
from core.store import Store

import checkin
import store_ext
from sweep import door_code_for

TODAY = "2026-09-01"
RES_REF = "R1"


def _store(tmp_path) -> tuple[Store, object]:
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / "t.db")
    store_ext.ensure_schema(store)
    pms_fields = {
        "guest_name": "Test Guest", "first_name": "Test", "email": "test@example.com",
        "phone": "", "language": "en", "party": 2, "room_type": "Classic Double",
        "room_number": "412", "nights": 2, "check_in": TODAY, "check_out": "2026-09-03",
        "channel": "Direct", "vip": False, "smart_lock": True, "payment_kind": "prepaid",
        "balance": 0.0, "city_tax": 0.0, "currency": "EUR",
    }
    store_ext.sync_guest(store, RES_REF, pms_fields, seed={
        "id_status": "verified", "waiver_signed": 1, "portal_step": "payment",
    })
    return store, settings


def _answer_pending_doorcode_prompt() -> None:
    pending = sub_data_dir("pending")
    prompts = list(pending.glob("doorcode-*.prompt.md"))
    assert len(prompts) == 1, f"expected exactly one pending doorcode prompt, got {prompts}"
    stem = prompts[0].name[: -len(".prompt.md")]
    answer = pending / f"{stem}.answer.json"
    answer.write_text(json.dumps({
        "subject": "Your door code for tonight",
        "body": "Hi Test, your door code for room 412 is ready. The team.",
    }), encoding="utf-8")


def test_door_code_pend_then_retry_drafts_instead_of_short_circuiting(tmp_path):
    store, settings = _store(tmp_path)
    row = store_ext.get_guest(store, RES_REF)
    expected_code = door_code_for(row["room_number"])
    now_iso = f"{TODAY}T15:00:00"
    external_id = f"{RES_REF}:doorcode-notice:{TODAY}"

    # First attempt pends - the interactive provider has no answer file yet.
    with pytest.raises(LLMPendingInteractive):
        checkin.maybe_issue_door_code(settings, store, row, today=TODAY, now_iso=now_iso,
                                      provider="interactive")

    # Nothing permanent was written before the draft resolved.
    row_after_pend = store_ext.get_guest(store, RES_REF)
    assert not row_after_pend["door_code"], "door_code must not be set before the draft succeeds"
    assert not any(e["kind"] == "door_code_issued"
                  for e in store_ext.list_checkin_events(store, RES_REF))

    # The item exists, still `new`, empty draft - and the pending code is
    # cached on its own payload under a `_`-prefixed key (survives the
    # payload refresh a retry's `upsert_item` call does).
    item = store.get_by_external("checkin", external_id)
    assert item is not None
    assert item.review_status == "new"
    assert not item.draft
    assert item.payload.get("_door_code") == expected_code

    # Answer the prompt exactly as a hotel's Claude session would, then retry.
    _answer_pending_doorcode_prompt()
    result = checkin.maybe_issue_door_code(settings, store, row_after_pend, today=TODAY,
                                           now_iso=now_iso, provider="interactive")

    assert result is not None and result.get("already_issued") is not True
    assert result["code"] == expected_code
    assert result["item"] == item.id, "the retry must resume the SAME item, not create a new one"

    item = store.get_item(item.id)
    assert item.review_status == "pending_review"
    assert item.draft and item.draft.get("body"), "the item must never sit at new with an empty draft"

    row_after_retry = store_ext.get_guest(store, RES_REF)
    assert row_after_retry["door_code"] == expected_code
    door_events = [e for e in store_ext.list_checkin_events(store, RES_REF)
                   if e["kind"] == "door_code_issued"]
    assert len(door_events) == 1, "the audit event must not be duplicated by the retry"

    # A third call must now take the already-issued fast path, not re-draft.
    result_again = checkin.maybe_issue_door_code(settings, store, row_after_retry, today=TODAY,
                                                  now_iso=now_iso, provider="interactive")
    assert result_again == {"eligible": True, "already_issued": True, "code": expected_code}
    store.close()
