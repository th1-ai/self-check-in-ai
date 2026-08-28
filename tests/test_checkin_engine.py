"""Tests for the I/O shell (tools/checkin.py) against the bundled fixtures,
with provider=mock. No network, no credentials. Mirrors the reference agent's
test shape - see factory/reference-agent/tests/test_agent_triage.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from core.config import load_settings
from core.llm import LLMPendingInteractive
from core.store import Store

import checkin
import store_ext

TODAY = "2026-09-01"


def _settings(**kw):
    return load_settings(provider="mock", mode="shadow", **kw)


def _store(tmp_path, name="t.db") -> Store:
    store = Store(_settings(), path=tmp_path / name)
    store_ext.ensure_schema(store)
    return store


def test_sweep_over_the_fixtures_matches_the_expected_shape(tmp_path):
    store = _store(tmp_path)
    outcome = checkin.process_sweep(_settings(), store, today=TODAY, provider="mock")
    sweep = outcome["sweep"]
    assert len(sweep.invites) == 3
    assert len(sweep.follow_ups) == 3
    assert len(sweep.skips) == 4
    assert outcome["stats"]["processed"] == 6
    assert outcome["stats"]["needs_human"] == 1  # MH-3010, unsupported language
    store.close()


def test_shadow_mode_never_sends_anything(tmp_path):
    store = _store(tmp_path)
    checkin.process_sweep(_settings(), store, today=TODAY, provider="mock")
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0
    store.close()


def test_rerun_same_day_does_not_double_draft(tmp_path):
    store = _store(tmp_path)
    settings = _settings()
    first = checkin.process_sweep(settings, store, today=TODAY, provider="mock")
    before = len(store.list_items(kind="checkin_invite", limit=100))
    second = checkin.process_sweep(settings, store, today=TODAY, provider="mock")
    after = len(store.list_items(kind="checkin_invite", limit=100))
    assert before == after == 6
    assert second["stats"]["processed"] == 0
    assert first["stats"]["processed"] == 6
    store.close()


def test_unsupported_language_is_flagged_needs_human_with_a_reason(tmp_path):
    store = _store(tmp_path)
    checkin.process_sweep(_settings(), store, today=TODAY, provider="mock")
    item = store.get_by_external("checkin", f"MH-3010:invite:{TODAY}")
    assert item is not None
    assert item.review_status == "needs_human"
    events = store.list_events(item.id)
    reasons = [e["detail"].get("reason", "") for e in events if e["detail"]]
    assert any("not in hotel.languages" in r for r in reasons)
    store.close()


def test_supported_language_reply_is_pending_review_not_needs_human(tmp_path):
    store = _store(tmp_path)
    checkin.process_sweep(_settings(), store, today=TODAY, provider="mock")
    item = store.get_by_external("checkin", f"MH-3003:invite:{TODAY}")
    assert item is not None
    assert item.review_status == "pending_review"
    store.close()


def test_needs_human_for_message_falls_back_to_default_language():
    settings = _settings()
    lang, needs_human, reason = checkin.needs_human_for_message("de", settings)
    assert lang == settings.hotel.default_language
    assert needs_human is True
    assert "not in hotel.languages" in reason


def test_needs_human_for_message_no_language_on_file_is_not_an_error():
    settings = _settings()
    lang, needs_human, reason = checkin.needs_human_for_message("", settings)
    assert needs_human is False
    assert reason == ""


def test_money_formats_whole_and_fractional_amounts():
    assert checkin.money(120, "EUR") == "EUR 120"
    assert checkin.money(128.5, "GBP") == "GBP 128.50"
    assert checkin.money(200.0, "NOK") == "NOK 200"


def test_retry_after_interactive_pend_resumes_the_same_guest(tmp_path, monkeypatch):
    """A pend during draft() must not advance invite_status or create a draft -
    the next pass must draft (not skip) the same guest. See
    docs/how-it-works.md "Idempotency"."""
    store = _store(tmp_path)
    settings = _settings()
    row = {
        "res_ref": "R-PEND", "guest_name": "Pending Guest", "first_name": "Pending",
        "email": "pending@example.com", "language": "en", "room_type": "Classic Double",
        "nights": 2, "check_in": "2026-09-05", "currency": "EUR", "payment_kind": "balance_due",
        "balance": 100.0, "city_tax": 3.0,
    }
    calls = {"n": 0}
    real_complete = checkin.complete

    def flaky_complete(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMPendingInteractive("pid", tmp_path / "p.md", None, tmp_path / "a.json")
        return real_complete(*a, **kw)

    monkeypatch.setattr(checkin, "complete", flaky_complete)

    with pytest.raises(LLMPendingInteractive):
        checkin.draft_and_queue_message(settings, store, row, "invite", today=TODAY,
                                        now_iso=f"{TODAY}T09:00:00+00:00", provider="mock",
                                        portal_base="https://checkin.example.com")

    parked = store.get_by_external("checkin", f"R-PEND:invite:{TODAY}")
    assert parked is not None and parked.review_status == "new" and parked.draft is None
    guest_row = store_ext.get_guest(store, "R-PEND")
    assert guest_row is None or guest_row["invite_status"] == "not_sent"

    item, did_work = checkin.draft_and_queue_message(
        settings, store, row, "invite", today=TODAY, now_iso=f"{TODAY}T09:00:00+00:00",
        provider="mock", portal_base="https://checkin.example.com")
    assert did_work is True
    assert item.draft is not None
    assert calls["n"] == 2
    store.close()
