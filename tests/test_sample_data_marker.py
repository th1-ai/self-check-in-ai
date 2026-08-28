"""Sample data must never look like the property's own bookings.

On a fresh clone every `systems.*.adapter` is still the shipped `mock`
default, so a REAL (not `make demo`) pass reads invented fixtures. Core tags
any item it creates from such a source with payload `_sample: True`
(`core.store.Store.upsert_item` via `core.adapters.is_sample_source`;
`item.is_sample` reads it back) - this repo does not re-implement the tagging,
it only has to SHOW it. These tests pin that showing: `make review` prints a
`[SAMPLE DATA]` marker in both `list` and `show`.

`tests/conftest.py`'s autouse `_isolated_repo` fixture points
AGENT_CONFIG_DIR/AGENT_REPO_ROOT at temp copies, so nothing here reads or
writes the hotel's own config or data.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store
from tools.review import cmd_list, cmd_show


def _queued_sample_item(tmp_path):
    """One `checkin_invite` waiting for a human, built on the day-one config:
    a real pass whose `systems.pms.adapter` is still the shipped `mock`."""
    settings = load_settings()
    assert settings.systems.pms.adapter == "mock"   # the shipped default
    assert settings.demo is False                   # the real path, not `make demo`
    store = Store(settings, path=tmp_path / "test.db")
    item = store.upsert_item(
        "pms", "MH-3001:invite:2026-09-03", kind="checkin_invite",
        payload={"res_ref": "MH-3001", "message_kind": "invite",
                 "guest_name": "Lena Novak", "email": "lena@example.com"})
    item = store.transition(item.id, "pending_review", actor="agent")
    return store, item


def test_an_item_from_the_mock_pms_is_tagged_sample(tmp_path):
    store, item = _queued_sample_item(tmp_path)
    store.close()
    assert item.is_sample is True
    assert item.payload.get("_sample") is True


def test_review_list_flags_the_sample_item(tmp_path, capsys):
    store, item = _queued_sample_item(tmp_path)
    capsys.readouterr()  # discard anything written while setting up
    cmd_list(store, SimpleNamespace(status=None, kind=None, limit=50))
    store.close()
    out = capsys.readouterr().out
    assert "[SAMPLE DATA]" in out                       # on the item's own line
    assert "not your property" in out                   # and the footer explains why


def test_review_show_warns_before_the_json(tmp_path, capsys):
    store, item = _queued_sample_item(tmp_path)
    capsys.readouterr()
    cmd_show(store, SimpleNamespace(id=item.id))
    store.close()
    out = capsys.readouterr().out
    assert out.startswith("[SAMPLE DATA]")
