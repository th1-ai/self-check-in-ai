#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Uses `load_settings(demo=True)`: mock provider, shadow mode, and the mock
adapter for every system, whatever config/hotel.yaml says - a demo can never
read a real mailbox or PMS. Runs against its own database
(`data/demo/demo.db`) so running it twice always shows the same picture, and
never touches `data/agent.db` (that is `make run`'s file).

Walks both halves of the agent: the pre-arrival sweep over
`fixtures/hotel/reservations.json` (invite / follow-up / skip, every skip
with a reason), then the portal-sync leg over `fixtures/inbound/*.json`
(ID check, waiver, payment, an upsell, and the door-code showcase guest -
plus the two escalations that show the front-desk handoff actually working).
Nothing is sent: mode is shadow, and demo never calls `tools/review.py send`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store  # noqa: E402

from checkin import process_sweep  # noqa: E402
import portal_sync  # noqa: E402
import store_ext  # noqa: E402

# Fixed so the demo never depends on the real wall-clock date - fixtures/hotel
# and fixtures/inbound are all dated around this anchor. Real runs use the
# actual date (tools/run.py, tools/portal_sync.py, no --today).
DEMO_TODAY = "2026-09-01"


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    store = Store(settings, path=demo_db)
    store_ext.ensure_schema(store)

    print(f"Self Check-In AI demo - The Gatekeeper - {DEMO_TODAY}\n")
    print("=== Pre-arrival sweep (tools/run.py) ===\n")
    outcome = process_sweep(settings, store, today=DEMO_TODAY, provider="mock")
    sweep = outcome["sweep"]
    print(sweep.headline() + "\n")
    for action in sweep.actions:
        print(f"  {action.kind:9} {action.guest.res_ref:9} {action.guest.guest_name:16} "
             f"{action.reason}")
    for skip in sweep.skips:
        print(f"  {'skip':9} {skip.guest.res_ref:9} {skip.guest.guest_name:16} {skip.reason}")

    print("\nNothing was sent: mode is shadow, and demo never calls send() at all.")
    print("Run `make review` to see the drafts, or read workflows/10-checkin-sweep.md.\n")

    print("=== Portal sync (tools/portal_sync.py) ===\n")
    code, sync_stats = portal_sync.one_pass(settings, store, provider="mock", today=DEMO_TODAY,
                                            demo=True, reset_cursor=False)
    if code != 0:
        print("portal sync did not finish cleanly", file=sys.stderr)
        return code

    stats = {
        "processed": outcome["stats"]["processed"] + sync_stats["processed"],
        "drafted": outcome["stats"]["drafted"] + sync_stats["drafted"],
        "sent": 0,
    }
    print(f"\n{sync_stats['escalated']} portal event(s) were handed to the front desk - see "
         "`python3 tools/review.py list --status needs_human --kind checkin_escalation`.")
    print("Every folio note, door-code message and guest email above is queued, not sent - "
         "see docs/safety.md.\n")

    print(f"DEMO OK — {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
