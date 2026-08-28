"""Tests for tools/checkin.py:apply_event() - the portal sync leg. Builds the
failure branches the source demo never implemented (specs section 11.1) -
see docs/how-it-works.md "The portal sync leg"."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.config import load_settings
from core.store import Store

import checkin
import store_ext

TODAY = "2026-09-01"


def _store(tmp_path, name="t.db") -> Store:
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / name)
    store_ext.ensure_schema(store)
    return store, settings


_PMS_KEYS = {
    "guest_name", "first_name", "email", "phone", "language", "party", "room_type",
    "room_number", "nights", "check_in", "check_out", "channel", "vip", "smart_lock",
    "payment_kind", "balance", "city_tax", "currency",
}


def _seed_guest(store, res_ref="R1", **kw) -> None:
    """`kw` may set a PMS field (name, room, dates, money) or a mutable tracking
    field (portal_step, id_status, waiver_signed, card_status, door_code) - the
    two go to different arguments of `store_ext.sync_guest`, exactly like a
    real fixture's `seed_*` keys do (`tools/checkin.py:load_guests`)."""
    pms_fields = {
        "guest_name": "Test Guest", "first_name": "Test", "email": "test@example.com",
        "phone": "", "language": "en", "party": 2, "room_type": "Classic Double",
        "room_number": "101", "nights": 2, "check_in": "2026-09-01", "check_out": "2026-09-03",
        "channel": "Direct", "vip": False, "smart_lock": False, "payment_kind": "balance_due",
        "balance": 200.0, "city_tax": 4.0, "currency": "EUR",
    }
    seed = {}
    for key, value in kw.items():
        (pms_fields if key in _PMS_KEYS else seed)[key] = value
    store_ext.sync_guest(store, res_ref, pms_fields, seed=seed)


def test_id_mismatch_escalates_and_never_verifies(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store)
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "id_check", "id_match": False,
                          "id_name": "Wrong Name", "event_id": "e1"}, today=TODAY)
    assert result["ok"] is False
    assert result["escalated"]
    row = store_ext.get_guest(store, "R1")
    assert row["id_status"] == "failed"
    item = store.get_item(result["escalated"])
    assert item.review_status == "needs_human"
    assert item.kind == "checkin_escalation"
    store.close()


def test_id_match_verifies_and_advances_portal_step(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store)
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "id_check", "id_match": True,
                          "id_name": "Test Guest", "event_id": "e1"}, today=TODAY)
    assert result["ok"] is True
    row = store_ext.get_guest(store, "R1")
    assert row["id_status"] == "verified"
    assert row["portal_step"] == "waiver"
    store.close()


def test_waiver_declined_escalates(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store, id_status="verified", portal_step="waiver")
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "waiver", "signed": False, "event_id": "e2"},
        today=TODAY)
    assert result["ok"] is False
    row = store_ext.get_guest(store, "R1")
    assert row["waiver_signed"] == 0
    store.close()


def test_payment_failure_escalates_and_does_not_complete(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store, id_status="verified", waiver_signed=1, portal_step="payment")
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "payment", "result": "declined",
                          "event_id": "e3"}, today=TODAY)
    assert result["ok"] is False
    assert result["escalated"]
    row = store_ext.get_guest(store, "R1")
    assert row["portal_step"] == "payment"
    store.close()


def test_payment_success_completes_and_issues_a_door_code_for_an_eligible_guest(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store, id_status="verified", waiver_signed=1, portal_step="payment",
               smart_lock=True, room_number="412", check_in=TODAY, payment_kind="prepaid",
               balance=0.0, city_tax=0.0)
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "payment", "result": "authorized",
                          "amount": 0, "event_id": "e4"}, today=TODAY, provider="mock")
    assert result["ok"] is True
    row = store_ext.get_guest(store, "R1")
    assert row["portal_step"] == "completed"
    assert row["card_status"] == "authorized"
    assert row["door_code"] == "5-6-2-8"
    events = store_ext.list_checkin_events(store, "R1")
    assert any(e["kind"] == "door_code_issued" for e in events)
    door = result["door_code"]
    assert door["eligible"] is True and door["code"] == "5-6-2-8"
    store.close()


def test_payment_success_no_door_code_for_a_non_smart_lock_guest(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store, id_status="verified", waiver_signed=1, portal_step="payment",
               smart_lock=False, check_in=TODAY)
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "payment", "result": "charged",
                          "amount": 200, "event_id": "e5"}, today=TODAY, provider="mock")
    assert result["ok"] is True
    assert result["door_code"] is None
    row = store_ext.get_guest(store, "R1")
    assert row["door_code"] is None
    store.close()


def test_upsell_not_confirmed_is_ignored(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store, portal_step="completed")
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "upsell", "slug": "wine-in-room",
                          "confirmed": False, "event_id": "e6"}, today=TODAY)
    assert result["kind"] == "upsell_ignored"
    assert not store.list_items(kind="checkin_upsell", limit=10)
    store.close()


def test_upsell_confirmed_queues_a_pending_review_item(tmp_path):
    store, settings = _store(tmp_path)
    _seed_guest(store, portal_step="completed")
    result = checkin.apply_event(
        settings, store, {"res_ref": "R1", "kind": "upsell", "slug": "wine-in-room",
                          "confirmed": True, "event_id": "e7"}, today=TODAY)
    items = store.list_items(kind="checkin_upsell", limit=10)
    assert len(items) == 1
    assert items[0].review_status == "pending_review"
    assert items[0].payload["price"] > 0
    store.close()


def test_unknown_reservation_is_reported_not_crashed(tmp_path):
    store, settings = _store(tmp_path)
    result = checkin.apply_event(
        settings, store, {"res_ref": "NOPE", "kind": "id_check", "id_match": True,
                          "event_id": "e8"}, today=TODAY)
    assert result["ok"] is False
    assert "run the sweep first" in result["note"]
    store.close()
