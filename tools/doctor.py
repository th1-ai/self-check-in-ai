#!/usr/bin/env python3
"""tools/doctor.py - is Self Check-In AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus checks
specific to this agent: the windows/rules block, the prompt files, the
check-in policy knowledge file, and the portal events source. Exits 0 when
everything passed, 1 when a FAIL line needs fixing. Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings, repo_root  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402


def check_windows_and_rules(settings: Settings) -> list[Check]:
    out = []
    windows = settings.agent_get("windows", {})
    if not windows:
        out.append(Check("agent windows", FAIL, "no windows: block in config/agent.yaml",
                         "Copy config/agent.example.yaml to config/agent.yaml - it ships "
                         "with invite_window_days, chase_window_days and the rest."))
    else:
        out.append(Check("agent windows",
                         PASS, f"invite {windows.get('invite_window_days')}d, chase "
                         f"{windows.get('chase_window_days')}d, cooldown "
                         f"{windows.get('chase_cooldown_days')}d, room ready "
                         f"{windows.get('room_ready_hour')}:00"))
    rules = settings.agent_get("rules", {})
    if not rules:
        out.append(Check("agent rules", FAIL, "no rules: block in config/agent.yaml", ""))
    else:
        off = [k for k, v in rules.items()
              if not (v.get("enabled", True) if isinstance(v, dict) else bool(v))]
        detail = f"{len(rules)} switches" + (f", off: {', '.join(off)}" if off else ", all on")
        out.append(Check("agent rules", PASS, detail))
    return out


def check_prompts() -> Check:
    missing = [p for p in ("prompts/invite.md", "prompts/doorcode.md",
                           "prompts/schemas/invite.json", "prompts/schemas/doorcode.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "invite.md + doorcode.md + schemas present")


def check_checkin_policy() -> Check:
    path = repo_root() / "knowledge" / "checkin-policy.md"
    example = repo_root() / "knowledge" / "checkin-policy.example.md"
    if path.exists():
        return Check("check-in policy", PASS, "knowledge/checkin-policy.md")
    if example.exists():
        return Check("check-in policy", WARN, "using the shipped example",
                     "Copy knowledge/checkin-policy.example.md to knowledge/checkin-policy.md "
                     "and fill in your own ID-check law, waiver text and lock vendor.")
    return Check("check-in policy", FAIL, "knowledge/checkin-policy.example.md is missing", "")


def check_portal_events_source() -> Check:
    path = repo_root() / "data" / "imports" / "checkin_portal_events.jsonl"
    if path.exists():
        n = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        return Check("portal events source", PASS, f"{n} event(s) in {path}")
    return Check("portal events source", WARN, f"{path} does not exist yet",
                 "Fine until your check-in portal is wired up - see "
                 "docs/integrations.md#guest-check-in-portal. tools/portal_sync.py will pick "
                 "up events the moment this file exists.")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Self Check-In AI - doctor")

    checks = run_checks(settings, extra=[check_windows_and_rules])
    checks.append(check_prompts())
    checks.append(check_checkin_policy())
    checks.append(check_portal_events_source())
    return print_table(checks, title="Self Check-In AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
