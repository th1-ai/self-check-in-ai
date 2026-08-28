#!/usr/bin/env python3
"""tools/run.py - Self Check-In AI's pre-arrival sweep: invite -> chase -> skip.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --limit 5
    python3 tools/run.py --once --provider mock
    python3 tools/run.py --once --today 2026-09-01   # pin "today" (tests, a rehearsal)

One pass over every upcoming arrival: decide invite / follow-up / skip
(`tools/sweep.py`, deterministic), draft each message in the guest's own
language, and queue it for review. Never sends on its own -
`workflows/80-review.md` and `docs/safety.md` cover the review queue and the
shadow/live switch. The portal-sync leg is a separate job -
`python3 tools/portal_sync.py`, `workflows/15-portal-sync.md`.

Exit codes: 0 ok, 3 waiting on an `interactive` answer (see the message),
1 a real error.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive  # noqa: E402
from core.log import Run, summary_line  # noqa: E402
from core.store import Store  # noqa: E402

from checkin import process_sweep  # noqa: E402
import store_ext  # noqa: E402


def one_pass(settings, store, *, provider: str | None, today: str | None) -> tuple[int, dict]:
    with Run("checkin-sweep", settings, store) as run:
        try:
            outcome = process_sweep(settings, store, today=today, provider=provider)
        except LLMPendingInteractive as exc:
            run.stats = {"processed": 0, "drafted": 0, "sent": 0}
            print(str(exc))
            return 3, run.stats
        reaped = store.reap_stuck_sending()
        if reaped:
            print(f"[warn ] reaped {len(reaped)} item(s) stuck in 'sending'")
        run.stats = dict(outcome["stats"])
        print(outcome["sweep"].headline())
        for skip in outcome["sweep"].skips:
            print(f"  skip {skip.guest.res_ref}: {skip.reason}")
        for action in outcome["sweep"].actions:
            print(f"  {action.kind:9} {action.guest.res_ref}: {action.reason}")
    return 0, run.stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--limit", type=int, default=None,
                        help="present for parity with other agents; the sweep processes every "
                             "due guest in one pass, so this is currently unused")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--today", default=None,
                        help="pin 'today' (YYYY-MM-DD) instead of the real date - tests and "
                             "rehearsals only; a real run should never set this")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 3600)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    # --dry-run is a rehearsal: compute everything, write nothing - not even to
    # this repo's own data/agent.db. An ephemeral in-memory database gives every
    # tool call somewhere real to write during the pass (so the code path is
    # exercised exactly as normal) while guaranteeing nothing lands on disk and
    # nothing from one --dry-run pass can collide with the next one (no rows,
    # no IntegrityError, ever - each pass starts from empty).
    store = Store(settings, path=":memory:" if settings.dry_run else None)
    store_ext.ensure_schema(store)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 3600))
            while True:
                code, stats = one_pass(settings, store, provider=args.provider, today=args.today)
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = one_pass(settings, store, provider=args.provider, today=args.today)
        print(summary_line(stats, settings.mode))
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
