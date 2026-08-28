#!/usr/bin/env python3
"""tools/report.py - what the agent did, and what it cost.

    make report
    python3 tools/report.py
    python3 tools/report.py --since 2026-08-01

The KPI strip from the source demo (`specs/self-check-in-ai.md` section 4),
computed from `checkin_guests` + `checkin_events` + `items`, not asserted:
arrivals in the invite window, the online check-in rate, how many are still
mid-portal, upsell revenue actually posted to the folio, and desk minutes
saved (`windows.desk_minutes_saved` x completed check-ins). Also the queue
counts and, when `llm.provider` is `anthropic`/`claude-code`, LLM spend
(`core.store.Store.usage_totals`).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.review import queue_summary  # noqa: E402
from core.store import Store  # noqa: E402

from checkin import money  # noqa: E402
import store_ext  # noqa: E402


def build_report(settings, store, *, today: str | None = None) -> dict:
    today_str = today or date.today().isoformat()
    windows = settings.agent_get("windows", {}) or {}
    desk_minutes = int(windows.get("desk_minutes_saved", 6))
    horizon = (date.fromisoformat(today_str) + timedelta(
        days=int(windows.get("invite_window_days", 30)))).isoformat()

    guests = store_ext.list_guests(store)
    arrivals_in_window = [g for g in guests if today_str <= g["check_in"] <= horizon]
    completed = [g for g in guests if g["portal_step"] == "completed"]
    awaiting = [g for g in guests if g["portal_step"] not in ("none", "completed")]

    events = store_ext.list_checkin_events(store, limit=2000)
    folio_events = [e for e in events if e["kind"] == "folio_charge"]
    upsell_revenue = sum(float(e["amount"] or 0) for e in folio_events)
    door_codes = [e for e in events if e["kind"] == "door_code_issued"]
    escalations = [e for e in events if e["kind"].endswith(("_failed", "_declined"))]

    online_rate = (len(completed) / len(arrivals_in_window) * 100) if arrivals_in_window else 0.0

    return {
        "arrivals_in_window": len(arrivals_in_window),
        "checked_in_online_pct": round(online_rate, 1),
        "awaiting_completion": len(awaiting),
        "upsell_revenue": round(upsell_revenue, 2),
        "desk_minutes_saved": len(completed) * desk_minutes,
        "door_codes_issued": len(door_codes),
        "escalations": len(escalations),
        "queue": queue_summary(store),
        "usage": store.usage_totals(),
    }


def print_report(report: dict, currency: str) -> None:
    print("Self Check-In AI - report\n")
    print(f"  Arrivals in the invite window     {report['arrivals_in_window']}")
    print(f"  Checked in online                 {report['checked_in_online_pct']}%")
    print(f"  Awaiting completion                {report['awaiting_completion']}")
    print(f"  Portal upsell revenue              {money(report['upsell_revenue'], currency)}")
    print(f"  Desk minutes saved                 {report['desk_minutes_saved']}")
    print(f"  Door codes issued                  {report['door_codes_issued']}")
    print(f"  Escalations to the front desk      {report['escalations']}")
    q = report["queue"]
    print(f"\n  Waiting on a human                 {q['waiting_on_human']}")
    print(f"  In the send queue                  {q['in_send_queue']}")
    print(f"  Sent                               {q['sent']}")
    usage = report["usage"]
    if usage.get("calls"):
        print(f"\n  LLM calls                          {usage['calls']}")
        print(f"  LLM spend                          USD {usage['cost_usd']:.4f}")
    else:
        print("\n  LLM spend                          $0 (mock/interactive provider)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--today", default=None, help="pin 'today' - tests only")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        report = build_report(settings, store, today=args.today)
        if args.json:
            import json
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print_report(report, settings.hotel.currency)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
