"""Regression tests for the `csv` PMS adapter path through `tools/checkin.py`.

Covers two SIMULATION.md findings that only show up when reservations are
read through the *real* `CsvPMS` adapter (not `mock`, which already hands
back typed Python values):

- finding 3: `core/adapters/pms_csv.py:_to_reservation()` used to build the
  reservation's `Guest` with no `language=` at all, so a Dutch guest always
  produced an English draft with no `needs_human` flag - silent. Core now
  fills `guest.language` (`factory/core`, synced in); this file checks that
  `tools/checkin.py` actually uses it end to end and that BOTH outcomes
  (hotel supports the language / hotel does not) are disclosed, never silent.
- finding 4: `smart_lock` was read with plain `bool()` on the raw CSV
  string, so `"0"` (a non-empty string) came out `True`. `tools/checkin.py`
  now uses `_truthy()`, mirroring `core/adapters/pms_csv.py:_bool()`'s rule.
"""

from __future__ import annotations

import os
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

RESERVATIONS_HEADER = (
    "id,external_ref,status,check_in,check_out,room_type_id,room_type_name,room_id,"
    "adults,children,source,total,balance,currency,guest_email,guest_first_name,"
    "guest_last_name,guest_phone,guest_country,guest_language,smart_lock,"
    "payment_kind,city_tax\n"
)


def _use_csv_pms() -> None:
    """Flip the isolated sandbox's `hotel.yaml` from `mock` to the `csv` adapter.

    `tests/conftest.py`'s autouse fixture already copied
    `config/hotel.example.yaml` into `$AGENT_CONFIG_DIR/hotel.yaml` before
    this test ran - edit that copy in place, never the hotel's own file.
    """
    cfg_dir = Path(os.environ["AGENT_CONFIG_DIR"])
    hotel_yaml = cfg_dir / "hotel.yaml"
    text = hotel_yaml.read_text(encoding="utf-8")
    marker = "adapter: mock                       # mock | csv | cloudbeds | cli"
    assert marker in text, "hotel.example.yaml's pms adapter line changed shape"
    hotel_yaml.write_text(text.replace(marker, "adapter: csv"), encoding="utf-8")


def _write_reservations(rows: list[str]) -> None:
    imports = Path(os.environ["AGENT_REPO_ROOT"]) / "data" / "imports"
    imports.mkdir(parents=True, exist_ok=True)
    (imports / "reservations.csv").write_text(RESERVATIONS_HEADER + "".join(rows),
                                               encoding="utf-8")


def _store(tmp_path, name="t.db") -> Store:
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / name)
    store_ext.ensure_schema(store)
    return store, settings


def test_dutch_guest_via_csv_is_disclosed_not_silently_english(tmp_path):
    """hotel.example.yaml's languages are [en, pt, es] - no `nl` - so this
    guest's language is ON the booking but NOT supported: the draft must
    fall back to the default language AND flag `needs_human`, never just
    silently draft in English with no flag (SIMULATION.md finding 3)."""
    _use_csv_pms()
    _write_reservations([
        "R-NL,R-NL,confirmed,2026-09-05,2026-09-07,classic,Classic Double,201,"
        "1,0,Direct,200,200,EUR,nl@example.com,Anke,Devries,,NL,nl,0,"
        "balance_due,4\n",
    ])
    store, settings = _store(tmp_path)
    rows = checkin.load_guests(settings, store, today=TODAY)
    assert len(rows) == 1
    row = rows[0]
    assert row["language"] == "nl", "guest.language was dropped again on the way in"

    item, did_work = checkin.draft_and_queue_message(
        settings, store, row, "invite", today=TODAY, now_iso=f"{TODAY}T09:00:00",
        provider="mock", portal_base="https://portal.example.com")
    assert did_work is True
    assert item.review_status == "needs_human"
    assert item.payload["language"] == "en"  # hotel's default, not a guess at Dutch
    # The disclosed reason lands in the audit trail - never a silent default.
    reasons = [e["detail"] for e in store.list_events(item.id) if e["action"] == "status:needs_human"]
    assert any("nl" in str(d) and "hotel.languages" in str(d) for d in reasons), reasons
    store.close()


def test_dutch_guest_via_csv_drafts_in_dutch_when_the_hotel_supports_it(tmp_path):
    """Same guest, but the hotel added `nl` to `hotel.languages` - now the
    draft language is the guest's own, `needs_human` is False, and the
    choice is still explicit (nothing here is a coincidence of dropped
    data)."""
    _use_csv_pms()
    cfg_dir = Path(os.environ["AGENT_CONFIG_DIR"])
    hotel_yaml = cfg_dir / "hotel.yaml"
    text = hotel_yaml.read_text(encoding="utf-8")
    assert "languages: [en, pt, es]" in text
    hotel_yaml.write_text(text.replace("languages: [en, pt, es]", "languages: [en, nl]"),
                          encoding="utf-8")
    _write_reservations([
        "R-NL,R-NL,confirmed,2026-09-05,2026-09-07,classic,Classic Double,201,"
        "1,0,Direct,200,200,EUR,nl@example.com,Anke,Devries,,NL,nl,0,"
        "balance_due,4\n",
    ])
    store, settings = _store(tmp_path)
    rows = checkin.load_guests(settings, store, today=TODAY)
    row = rows[0]
    assert row["language"] == "nl"

    item, did_work = checkin.draft_and_queue_message(
        settings, store, row, "invite", today=TODAY, now_iso=f"{TODAY}T09:00:00",
        provider="mock", portal_base="https://portal.example.com")
    assert did_work is True
    assert item.review_status == "pending_review"
    assert item.payload["language"] == "nl"
    store.close()


def test_smart_lock_csv_boolean_is_parsed_with_a_real_truthy_rule(tmp_path):
    """`smart_lock=0` (a non-empty string) must read as False, not
    `bool("0")`'s `True` (SIMULATION.md finding 4). Mixed values on
    purpose - every shape a CSV export shows up with."""
    _use_csv_pms()
    _write_reservations([
        "R-OFF-0,R-OFF-0,confirmed,2026-09-05,2026-09-07,classic,Classic Double,201,"
        "1,0,Direct,200,200,EUR,a@example.com,A,One,,NL,en,0,balance_due,0\n",
        "R-OFF-FALSE,R-OFF-FALSE,confirmed,2026-09-05,2026-09-07,classic,Classic Double,202,"
        "1,0,Direct,200,200,EUR,b@example.com,B,Two,,NL,en,false,balance_due,0\n",
        "R-OFF-EMPTY,R-OFF-EMPTY,confirmed,2026-09-05,2026-09-07,classic,Classic Double,203,"
        "1,0,Direct,200,200,EUR,c@example.com,C,Three,,NL,en,,balance_due,0\n",
        "R-ON-1,R-ON-1,confirmed,2026-09-05,2026-09-07,classic,Classic Double,204,"
        "1,0,Direct,200,200,EUR,d@example.com,D,Four,,NL,en,1,balance_due,0\n",
        "R-ON-TRUE,R-ON-TRUE,confirmed,2026-09-05,2026-09-07,classic,Classic Double,205,"
        "1,0,Direct,200,200,EUR,e@example.com,E,Five,,NL,en,true,balance_due,0\n",
    ])
    store, settings = _store(tmp_path)
    # `checkin_guests.smart_lock` is a SQLite INTEGER column, so the row that
    # comes back from `store_ext.sync_guest` (via `load_guests`) is 0/1, not
    # a Python bool - `bool(...)` on it is the fair comparison, exactly like
    # `tools/checkin.py`'s own `if not row.get("smart_lock")` gate uses.
    rows = {r["res_ref"]: r for r in checkin.load_guests(settings, store, today=TODAY)}
    assert bool(rows["R-OFF-0"]["smart_lock"]) is False
    assert bool(rows["R-OFF-FALSE"]["smart_lock"]) is False
    assert bool(rows["R-OFF-EMPTY"]["smart_lock"]) is False
    assert bool(rows["R-ON-1"]["smart_lock"]) is True
    assert bool(rows["R-ON-TRUE"]["smart_lock"]) is True
    store.close()
