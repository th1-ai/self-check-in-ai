#!/usr/bin/env python3
"""tools/portal_sync.py - read back what the guest did in the check-in portal.

    python3 tools/portal_sync.py --once
    python3 tools/portal_sync.py --once --dry-run
    python3 tools/portal_sync.py --once --today 2026-09-01

Reads new completion events (ID result, waiver, payment, an upsell tap) and
applies them: updates `checkin_guests`, writes the `checkin_events` audit
feed, escalates a failure to the front desk, and (once id + waiver + payment
have all succeeded) computes a door code for an eligible smart-lock guest and
queues the guest's notice. See docs/how-it-works.md "The portal sync leg" and
docs/integrations.md#guest-check-in-portal for where events come from - this
repo does not build the guest-facing portal itself.

In demo mode (`make demo`) events are read from `fixtures/inbound/*.json`. In
real use they are read from `data/imports/checkin_portal_events.jsonl` -
newline-delimited JSON, one event per line, appended by your own portal (or a
webhook relay) and never edited by hand. A `kv` cursor
(`portal_sync:file_offset`) means a real run only reads what was appended
since the last pass; `--reset-cursor` re-reads the whole file.

Exit codes: 0 ok, 3 waiting on an `interactive` answer, 1 a real error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings, repo_root, sub_data_dir  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive  # noqa: E402
from core.log import Run, get_logger  # noqa: E402
from core.store import Store  # noqa: E402

from checkin import apply_event  # noqa: E402
import store_ext  # noqa: E402

log = get_logger("portal_sync")

CURSOR_KEY = "portal_sync:file_offset"
EVENTS_FILE = "data/imports/checkin_portal_events.jsonl"


def _demo_events() -> list[tuple[None, dict]]:
    """Demo/test source: every `fixtures/inbound/*.json` file, in name order.

    No cursor applies to the demo path (`tools/demo.py` always reads the
    whole fixture set), so each event carries no cursor offset (`None`).
    """
    out = []
    inbound = repo_root() / "fixtures" / "inbound"
    for path in sorted(inbound.glob("*.json")):
        try:
            out.append((None, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return out


def _real_events(store: Store, *, reset_cursor: bool) -> list[tuple[int, dict]]:
    """Real source: new lines appended to `data/imports/checkin_portal_events.jsonl`.

    Returns `(end_offset, event)` pairs - `end_offset` is how far into the
    file that event's line ends, in the same character units `CURSOR_KEY`
    is stored in. Does NOT move the cursor itself: the cursor must advance
    only after `one_pass()` has fully applied a given event, never before
    (SIMULATION.md finding 1). Advancing it here, before any event in the
    batch was applied, is exactly the bug that dropped 7 of 11 events on a
    pause: `LLMPendingInteractive` raised on event 5, but the cursor was
    already past event 11.
    """
    path = repo_root() / EVENTS_FILE
    if not path.exists():
        return []
    offset = 0 if reset_cursor else int(store.get_cursor(CURSOR_KEY, 0) or 0)
    text = path.read_text(encoding="utf-8")
    events: list[tuple[int, dict]] = []
    pos = offset
    for raw_line in text[offset:].splitlines(keepends=True):
        pos += len(raw_line)
        line = raw_line.strip()
        if not line:
            continue
        try:
            events.append((pos, json.loads(line)))
        except json.JSONDecodeError as exc:
            log.warn("bad event line, skipped", error=str(exc))
    return events


def one_pass(settings, store, *, provider: str | None, today: str | None, demo: bool,
            reset_cursor: bool) -> tuple[int, dict]:
    today_str = today or date.today().isoformat()
    stats = {"processed": 0, "drafted": 0, "sent": 0, "needs_human": 0, "escalated": 0}
    events = _demo_events() if demo else _real_events(store, reset_cursor=reset_cursor)
    with Run("portal-sync", settings, store) as run:
        for cursor_offset, event in events:
            event_id = str(event.get("event_id") or "")
            if event_id and store.get("portal_sync:seen:" + event_id):
                # Already fully applied on an earlier pass (only reachable via
                # --reset-cursor, which re-reads lines the cursor already
                # skipped). Nothing to do, but the cursor still moves past it
                # so a later plain pass does not re-read it again.
                if cursor_offset is not None:
                    store.set_cursor(CURSOR_KEY, cursor_offset)
                continue
            try:
                result = apply_event(settings, store, event, today=today_str, provider=provider)
            except LLMPendingInteractive as exc:
                # Do NOT advance the cursor: this event (and everything after
                # it in this batch) has not been applied yet. The cursor stays
                # at the end of the last event that WAS fully applied, so the
                # next pass re-reads from here - this event included.
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            stats["processed"] += 1
            if result.get("escalated"):
                stats["escalated"] += 1
                stats["needs_human"] += 1
            if result.get("kind") in ("upsell_queued",) or (
                    isinstance(result.get("door_code"), dict) and result["door_code"].get("item")):
                stats["drafted"] += 1
            if event_id:
                store.set("portal_sync:seen:" + event_id, True)
            # The cursor advances only now - after this event is fully
            # applied - never before (see `_real_events()`'s docstring).
            if cursor_offset is not None:
                store.set_cursor(CURSOR_KEY, cursor_offset)
            print(f"  {result.get('res_ref', '?'):10} {result.get('kind', '?')}"
                 + (f"  -> escalated {result['escalated']}" if result.get("escalated") else ""))
        run.stats = dict(stats)
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run a single pass (default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--today", default=None,
                        help="pin 'today' (YYYY-MM-DD) - tests and rehearsals only")
    parser.add_argument("--reset-cursor", action="store_true",
                        help="re-read the whole events file instead of only what is new")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    # --dry-run is a rehearsal: compute everything, write nothing - not even to
    # this repo's own data/agent.db. See tools/run.py for the same pattern.
    store = Store(settings, path=":memory:" if settings.dry_run else None)
    store_ext.ensure_schema(store)
    try:
        # The CLI always reads real events (data/imports/...). tools/demo.py
        # calls one_pass(..., demo=True) directly instead of going through
        # main(), so `make demo` never depends on argv parsing here.
        code, stats = one_pass(settings, store, provider=args.provider, today=args.today,
                               demo=False, reset_cursor=args.reset_cursor)
        print(f"{stats['processed']} events processed, {stats['drafted']} drafted, "
             f"{stats['escalated']} escalated ({settings.mode})")
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
