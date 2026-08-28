"""tools/checkin.py - the I/O shell around tools/sweep.py.

Loads arrivals from the PMS, syncs them into `checkin_guests`
(`tools/store_ext.py`), calls the pure `tools/sweep.py:run_sweep()`, and turns
the result into drafted, localized, queued review items. Also applies portal
completion events (`tools/portal_sync.py`'s job) and dispatches the actual
send for every kind of queued item (`tools/review.py send`).

Two LLM tasks live here, both schema-checked (`prompts/invite.md`,
`prompts/doorcode.md`) and both write ONLY the sentence - see
docs/how-it-works.md for why every decision above them is deterministic.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.adapters import get_email, get_pms, get_stub
from core.adapters.base import AdapterError, AdapterNotImplemented
from core.config import Settings
from core.llm import LLMSchemaError, complete
from core.log import get_logger
from core.review import WriteBlocked
from core.store import Item, Store
from core.templates import build_prompt

import store_ext
from sweep import CheckinGuest, door_code_activation, door_code_for, run_sweep

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{name}.json").read_text(encoding="utf-8"))


INVITE_SCHEMA = _schema("invite")
DOORCODE_SCHEMA = _schema("doorcode")

log = get_logger("checkin")


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------
def money(amount: float, currency: str) -> str:
    """`128.5, "EUR"` -> `"EUR 128.50"`; a whole number drops the decimals."""
    amount = round(float(amount), 2)
    if amount == int(amount):
        return f"{currency} {int(amount)}"
    return f"{currency} {amount:.2f}"


def _format_hour_12(hour: int) -> str:
    h = hour % 24
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:00 {suffix}"


def _now_for(today: str | None) -> datetime:
    """The sweep's "now". Real runs use the real clock; `--today` (tests, the
    demo) pins a synthetic 09:00 so the result never depends on the wall
    clock - see docs/how-it-works.md "What runs when"."""
    if today is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(f"{today}T09:00:00+00:00")


def _rules(settings: Settings) -> dict:
    return dict(settings.agent_get("rules", {}) or {})


def _rule_on(settings: Settings, key: str) -> bool:
    value = _rules(settings).get(key, True)
    return bool(value.get("enabled", True)) if isinstance(value, dict) else bool(value)


def _windows(settings: Settings) -> dict:
    from sweep import DEFAULT_WINDOWS
    return {**DEFAULT_WINDOWS, **(settings.agent_get("windows", {}) or {})}


def needs_human_for_message(language: str | None, settings: Settings) -> tuple[str, bool, str]:
    """Reply only in the hotel's languages - see the family rule in README.

    Returns `(language_to_write_in, needs_human, reason)`. No language on the
    booking is not an error - it just means "use the default".
    """
    hotel_langs = [str(l).lower() for l in settings.hotel.languages]
    if not language:
        return settings.hotel.default_language, False, ""
    lang = str(language).lower()
    if lang in hotel_langs:
        return lang, False, ""
    return (settings.hotel.default_language, True,
           f"guest's language is {lang}, not in hotel.languages")


def _amount_due(row: dict) -> float:
    if row["payment_kind"] == "balance_due":
        return float(row["balance"] or 0) + float(row["city_tax"] or 0)
    return 0.0


def _truthy(value: Any) -> bool:
    """A raw `extra` value read as a real boolean, not `bool(str)`.

    `extra` is the adapter's passthrough dict: for the `csv` adapter it is
    the raw CSV row (every value a string), for `mock`/`cloudbeds`/`cli` it
    may already be a real Python `bool` from typed fixture/API data. Plain
    `bool("0")` is `True` in Python, so a CSV column of `smart_lock=0` was
    silently read as smart-lock-enabled (SIMULATION.md finding 4). Same
    truthy rule as `core/adapters/pms_csv.py:_bool()`: "0"/"false"/"no"/""
    -> False, whatever the surrounding whitespace or case.
    """
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


# --------------------------------------------------------------------------
# loading arrivals
# --------------------------------------------------------------------------
def load_guests(settings: Settings, store: Store, *, today: str) -> list[dict]:
    """Sync every upcoming arrival into `checkin_guests`, return the rows.

    Only arrivals on or after `today` are considered - an in-house or
    departed guest is not this agent's job. A 90-day horizon is generous on
    purpose: it is wide enough to show the "outside the invite window" skip
    without the horizon itself becoming a second window to configure.
    """
    pms = get_pms(settings)
    horizon = (date.fromisoformat(today) + timedelta(days=90)).isoformat()
    rows: list[dict] = []
    for res in pms.list_reservations(today, horizon):
        if res.check_in < today:
            continue
        if str(res.status or "").lower() in ("cancelled", "canceled", "no_show"):
            continue
        extra = res.extra or {}
        pms_fields = {
            "guest_name": res.guest.full_name or res.guest.first_name or "Guest",
            "first_name": res.guest.first_name or (res.guest.full_name or "Guest").split(" ")[0],
            "email": res.guest.email, "phone": res.guest.phone, "language": res.guest.language,
            "party": int(res.adults or 0) + int(res.children or 0),
            "room_type": res.room_type_name, "room_number": res.room_id, "nights": res.nights,
            "check_in": res.check_in, "check_out": res.check_out, "channel": res.source,
            "vip": bool(res.guest.vip), "smart_lock": _truthy(extra.get("smart_lock")),
            "payment_kind": str(extra.get("payment_kind") or "balance_due"),
            "balance": float(res.balance or 0), "city_tax": float(extra.get("city_tax") or 0),
            "currency": res.currency or settings.hotel.currency,
        }
        seed = {
            "invite_status": extra.get("seed_invite_status"),
            "invited_at": extra.get("seed_invited_at"),
            "portal_step": extra.get("seed_portal_step"),
            "id_status": extra.get("seed_id_status"),
            "waiver_signed": (int(bool(extra["seed_waiver_signed"]))
                              if extra.get("seed_waiver_signed") is not None else None),
            "card_status": extra.get("seed_card_status"),
            "door_code": extra.get("seed_door_code"),
            "door_code_issued_at": extra.get("seed_door_code_issued_at"),
            "upsells_total": extra.get("seed_upsells_total"),
        }
        seed = {k: v for k, v in seed.items() if v is not None}
        row = store_ext.sync_guest(store, res.id, pms_fields, seed=seed)
        row["arrival_offset"] = (date.fromisoformat(row["check_in"])
                                 - date.fromisoformat(today)).days
        rows.append(row)
    return rows


def _to_sweep_guest(row: dict, now: datetime) -> CheckinGuest:
    invited_hours_ago = None
    if row.get("invited_at"):
        try:
            invited_hours_ago = (now - datetime.fromisoformat(row["invited_at"])
                                 ).total_seconds() / 3600
        except ValueError:
            invited_hours_ago = None
    return CheckinGuest(
        res_ref=row["res_ref"], guest_name=row["guest_name"],
        first_name=row["first_name"] or row["guest_name"].split(" ")[0],
        arrival_offset=row["arrival_offset"], invite_status=row["invite_status"],
        portal_step=row["portal_step"], invited_hours_ago=invited_hours_ago)


# --------------------------------------------------------------------------
# drafting + queueing (the sweep's two outbound message kinds)
# --------------------------------------------------------------------------
def draft_and_queue_message(settings: Settings, store: Store, row: dict, kind: str, *,
                            today: str, now_iso: str, provider: str | None = None,
                            portal_base: str, stall_label: str = "") -> tuple[Item, bool]:
    """`kind` is `invite` or `follow_up`. Returns `(item, did_work)`.

    Idempotent per `(res_ref, kind, today)`: a second call the same day
    returns the existing item untouched (`did_work=False`). The
    `checkin_guests.invite_status` marker is only written AFTER the draft
    call succeeds, so an `LLMPendingInteractive` pause here (before that
    line runs) leaves the guest at `not_sent`/`invited` and a retry drafts
    again instead of the guest silently falling through - see
    docs/how-it-works.md "Idempotency".
    """
    external_id = f"{row['res_ref']}:{kind}:{today}"
    item = store.upsert_item(
        "checkin", external_id, kind="checkin_invite",
        payload={"res_ref": row["res_ref"], "message_kind": kind,
                "guest_name": row["guest_name"], "email": row["email"]})
    if item.review_status != "new":
        return item, False

    language, needs_human, reason = needs_human_for_message(row["language"], settings)
    amount_due = _amount_due(row)
    item_vars = {
        "kind": kind, "res_ref": row["res_ref"], "guest_name": row["guest_name"],
        "first_name": row["first_name"], "room_type": row["room_type"], "nights": row["nights"],
        "arrival_label": row["check_in"], "language": language,
        "amount_due": money(amount_due, row["currency"]) if amount_due else "",
        "payment_kind": row["payment_kind"],
        "portal_link": f"{portal_base.rstrip('/')}/{row['res_ref']}",
        "stall_label": stall_label,
    }
    prompt = build_prompt("invite", settings=settings, item=item_vars, fixture_id=external_id)
    try:
        result = complete("invite", prompt, INVITE_SCHEMA, settings=settings, provider=provider,
                          store=store, item_id=item.id, fixture_id=external_id)
    except LLMSchemaError as exc:
        store.set_fields(item.id, error=str(exc), payload={**item.payload, **item_vars})
        item = store.transition(item.id, "needs_human", actor="agent",
                                detail={"error": "invite_schema_error"})
        store_ext.set_invite_sent(store, row["res_ref"],
                                  "invited" if kind == "invite" else "reminded", now_iso)
        return item, True

    draft = result.data or {}
    store.set_fields(item.id, draft=draft, payload={**item.payload, **item_vars})
    status = "needs_human" if needs_human else "pending_review"
    item = store.transition(item.id, status, actor="agent",
                            detail={"reason": reason} if reason else None)
    store_ext.set_invite_sent(store, row["res_ref"], "invited" if kind == "invite" else "reminded",
                              now_iso)
    return item, True


def process_sweep(settings: Settings, store: Store, *, today: str | None = None,
                  provider: str | None = None) -> dict[str, Any]:
    """Run the whole pre-arrival sweep once. See tools/run.py for the CLI."""
    store_ext.ensure_schema(store)
    now = _now_for(today)
    today_str = today or now.date().isoformat()
    now_iso = now.isoformat(timespec="seconds")

    rows = load_guests(settings, store, today=today_str)
    by_ref = {r["res_ref"]: r for r in rows}
    sweep_guests = [_to_sweep_guest(r, now) for r in rows]
    result = run_sweep(sweep_guests, rules=_rules(settings), windows=_windows(settings))

    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "skipped": len(result.skips)}
    portal_base = str(settings.agent_get("portal_base_url") or "https://portal.example.com")
    for action in result.actions:
        row = by_ref[action.guest.res_ref]
        item, did_work = draft_and_queue_message(
            settings, store, row, action.kind, today=today_str, now_iso=now_iso,
            provider=provider, portal_base=portal_base, stall_label=action.guest.stall_label)
        if not did_work:
            continue
        stats["processed"] += 1
        stats["drafted"] += 1
        if item.review_status == "needs_human":
            stats["needs_human"] += 1
        log.info("queued", item_id=item.id, res_ref=action.guest.res_ref, kind=action.kind,
                 status=item.review_status)
    for skip in result.skips:
        store.record_event(None, "agent", "checkin_skip",
                           {"res_ref": skip.guest.res_ref, "reason": skip.reason})
        log.info("skip", res_ref=skip.guest.res_ref, reason=skip.reason)
    return {"stats": stats, "sweep": result, "guests": rows, "today": today_str}


# --------------------------------------------------------------------------
# escalations - the "hands that guest to the front desk" path (cant)
# --------------------------------------------------------------------------
def record_escalation(store: Store, res_ref: str, guest_name: str, reason: str, *,
                      event_id: str) -> Item:
    """A name mismatch, a failed card or an unsigned waiver, always `needs_human`.

    No draft is attached - resolving this is a real-world action (verify the
    ID at the desk, call the guest about the card). Close it once handled
    with `python3 tools/review.py reject <id> --reason "..."` - see
    workflows/80-review.md.
    """
    item = store.upsert_item("checkin", event_id, kind="checkin_escalation",
                             payload={"res_ref": res_ref, "guest_name": guest_name,
                                     "reason": reason})
    if item.review_status == "new":
        item = store.transition(item.id, "needs_human", actor="agent", detail={"reason": reason})
    return item


# --------------------------------------------------------------------------
# door codes - honest about hardware (docs/how-it-works.md)
# --------------------------------------------------------------------------
def maybe_issue_door_code(settings: Settings, store: Store, row: dict, *, today: str,
                          now_iso: str, provider: str | None = None) -> dict | None:
    """After a completed check-in, issue a door code if this guest is eligible.

    `eligible = smart_lock AND door-code-auto AND arrival_offset == 0`
    (`docs/how-it-works.md`). Computing the code is real; programming a real
    lock is not - `systems.locks` is always a stub in this family, so the
    attempt below is expected to be blocked or unimplemented, and that is
    handled, not an error.

    `checkin_guests.door_code` is the permanent "already issued" marker this
    function gates on below - so it is written only AFTER
    `draft_and_queue_doorcode()` returns a resolved item (drafted or
    escalated), never before. `draft_and_queue_doorcode()` can raise
    `LLMPendingInteractive` (the interactive provider) - if it does, that
    exception propagates from here unchanged and NOTHING below the call is
    reached: no `door_code` write, no audit event, no lock-stub attempt. A
    retry then finds `door_code` still unset, recomputes the same
    (deterministic) code and calls `draft_and_queue_doorcode()` again, which
    resumes the SAME item instead of creating a new one. Writing the marker
    first - the previous behaviour - made a retry short-circuit on
    `already_issued` before the notice was ever drafted, leaving the item
    stuck at `new` with an empty draft forever (SIMULATION.md finding 2).
    """
    if not row.get("smart_lock"):
        return None
    if not _rule_on(settings, "door-code-auto"):
        return None
    arrival_offset = (date.fromisoformat(row["check_in"]) - date.fromisoformat(today)).days
    if arrival_offset != 0:
        return {"eligible": False, "reason": "not arriving today"}
    if row.get("door_code"):
        return {"eligible": True, "already_issued": True, "code": row["door_code"]}

    code = door_code_for(row["room_number"])
    windows = _windows(settings)
    clock_hour = datetime.fromisoformat(now_iso).hour
    ready = door_code_activation(room_ready_hour=windows["room_ready_hour"], clock_hour=clock_hour)

    item, did_work = draft_and_queue_doorcode(
        settings, store, row, code=code, ready=ready, ready_hour=windows["room_ready_hour"],
        today=today, provider=provider)

    store_ext.set_portal_fields(store, row["res_ref"], door_code=code, door_code_issued_at=now_iso)
    store_ext.record_checkin_event(store, f"{row['res_ref']}:doorcode:{today}", row["res_ref"],
                                   "door_code_issued", detail=f"room {row['room_number']}")

    try:
        locks = get_stub("locks", settings)
        locks.issue_key(row["res_ref"], row["check_in"], row["check_out"], item=None)
    except (WriteBlocked, AdapterNotImplemented, AdapterError):
        pass  # expected - see the docstring above

    return {"eligible": True, "code": code, "ready": ready,
           "item": item.id if did_work else None}


def draft_and_queue_doorcode(settings: Settings, store: Store, row: dict, *, code: str,
                             ready: bool, ready_hour: int, today: str,
                             provider: str | None = None) -> tuple[Item, bool]:
    """Idempotent per `(res_ref, today)`, exactly like `draft_and_queue_message()`.

    `code`/`ready`/`ready_hour` are cached on the item's own payload under
    `_`-prefixed keys the moment the item is created - BEFORE the LLM call
    that can pend - so a retry that re-enters this function (via
    `maybe_issue_door_code()`) finds them already there (`upsert_item`'s
    payload refresh preserves `_`-prefixed keys) instead of needing the
    caller to have kept them around. `door_code` itself is never persisted
    to `checkin_guests` here - that write belongs to the caller, only after
    this function returns without raising - see
    `maybe_issue_door_code()`'s docstring.
    """
    external_id = f"{row['res_ref']}:doorcode-notice:{today}"
    item = store.upsert_item("checkin", external_id, kind="checkin_doorcode",
                             payload={"res_ref": row["res_ref"], "guest_name": row["guest_name"],
                                     "email": row["email"], "_door_code": code,
                                     "_door_code_ready": ready, "_door_code_ready_hour": ready_hour})
    if item.review_status != "new":
        return item, False

    language, needs_human, reason = needs_human_for_message(row["language"], settings)
    item_vars = {
        "res_ref": row["res_ref"], "guest_name": row["guest_name"],
        "first_name": row["first_name"], "room_number": row["room_number"], "door_code": code,
        "room_ready": ready, "ready_at": _format_hour_12(ready_hour), "language": language,
    }
    prompt = build_prompt("doorcode", settings=settings, item=item_vars, fixture_id=external_id)
    try:
        result = complete("doorcode", prompt, DOORCODE_SCHEMA, settings=settings,
                          provider=provider, store=store, item_id=item.id, fixture_id=external_id)
    except LLMSchemaError as exc:
        store.set_fields(item.id, error=str(exc), payload={**item.payload, **item_vars})
        item = store.transition(item.id, "needs_human", actor="agent",
                                detail={"error": "doorcode_schema_error"})
        return item, True

    draft = result.data or {}
    store.set_fields(item.id, draft=draft, payload={**item.payload, **item_vars})
    status = "needs_human" if needs_human else "pending_review"
    item = store.transition(item.id, status, actor="agent",
                            detail={"reason": reason} if reason else None)
    return item, True


def queue_upsell(store: Store, row: dict, *, title: str, price: float, currency: str,
                 event_id: str) -> Item:
    item = store.upsert_item(
        "checkin", f"{event_id}:upsell", kind="checkin_upsell",
        payload={"res_ref": row["res_ref"], "guest_name": row["guest_name"], "title": title,
                "price": price, "currency": currency})
    if item.review_status == "new":
        item = store.transition(item.id, "pending_review", actor="agent")
    return item


# --------------------------------------------------------------------------
# applying one portal completion event (tools/portal_sync.py's job)
# --------------------------------------------------------------------------
def apply_event(settings: Settings, store: Store, event: dict, *, today: str,
                provider: str | None = None) -> dict:
    """One portal completion event -> deterministic writes, maybe a queued item.

    The linear gate the source portal enforces (id -> waiver -> payment ->
    completed) is checked here instead of in a web UI - see
    docs/how-it-works.md "The portal sync leg". Every failure becomes a
    `checkin_escalation` item; nothing here ever guesses.
    """
    kind = str(event.get("kind") or "")
    res_ref = str(event.get("res_ref") or "")
    event_id = str(event.get("event_id") or f"{res_ref}:{kind}:{event.get('at', today)}")
    row = store_ext.get_guest(store, res_ref)
    if row is None:
        return {"ok": False, "res_ref": res_ref, "event_id": event_id,
                "note": "unknown reservation - run the sweep first so this guest is tracked"}
    guest_name = row["guest_name"]
    now_iso = _now_for(today).isoformat(timespec="seconds")

    if kind == "id_check":
        if not bool(event.get("id_match")):
            id_name = str(event.get("id_name") or "unknown")
            store_ext.set_portal_fields(store, res_ref, id_status="failed")
            store_ext.record_checkin_event(
                store, event_id, res_ref, "id_check_failed",
                detail=f"booking name '{guest_name}' vs ID name '{id_name}'")
            item = record_escalation(
                store, res_ref, guest_name,
                f"ID name mismatch: booking says '{guest_name}', ID says '{id_name}'",
                event_id=f"{event_id}:esc")
            return {"ok": False, "res_ref": res_ref, "kind": "id_check_failed", "escalated": item.id}
        store_ext.set_portal_fields(store, res_ref, id_status="verified", portal_step="waiver")
        store_ext.record_checkin_event(store, event_id, res_ref, "id_verified")
        return {"ok": True, "res_ref": res_ref, "kind": "id_verified"}

    if kind == "waiver":
        if not bool(event.get("signed", True)):
            store_ext.record_checkin_event(store, event_id, res_ref, "waiver_declined")
            item = record_escalation(
                store, res_ref, guest_name,
                "guest did not sign the registration card / damage waiver",
                event_id=f"{event_id}:esc")
            return {"ok": False, "res_ref": res_ref, "kind": "waiver_declined", "escalated": item.id}
        store_ext.set_portal_fields(store, res_ref, waiver_signed=True, portal_step="payment")
        store_ext.record_checkin_event(store, event_id, res_ref, "waiver_signed")
        return {"ok": True, "res_ref": res_ref, "kind": "waiver_signed"}

    if kind == "payment":
        result = str(event.get("result") or "")
        if result not in ("authorized", "charged"):
            store_ext.record_checkin_event(store, event_id, res_ref, "payment_failed",
                                           detail=result or "declined")
            item = record_escalation(
                store, res_ref, guest_name,
                f"card {result or 'declined'} at check-in - the balance was not collected",
                event_id=f"{event_id}:esc")
            return {"ok": False, "res_ref": res_ref, "kind": "payment_failed", "escalated": item.id}
        store_ext.set_portal_fields(store, res_ref, card_status=result, portal_step="completed")
        pms_kind = "card_authorized" if result == "authorized" else "payment"
        store_ext.record_checkin_event(store, event_id, res_ref, pms_kind,
                                       amount=event.get("amount"))
        if result == "charged":
            store_ext.record_checkin_event(store, f"{event_id}:pms", res_ref, "pms_update",
                                           detail="folio settled")
        door = maybe_issue_door_code(settings, store, store_ext.get_guest(store, res_ref) or row,
                                     today=today, now_iso=now_iso, provider=provider)
        return {"ok": True, "res_ref": res_ref, "kind": pms_kind, "door_code": door}

    if kind == "upsell":
        if not bool(event.get("confirmed")):
            return {"ok": True, "res_ref": res_ref, "kind": "upsell_ignored"}
        catalog = {u.get("slug"): u for u in (settings.agent_get("upsells", []) or [])}
        entry = catalog.get(event.get("slug"), {})
        title = str(entry.get("title") or event.get("slug") or "upsell")
        price = float(entry.get("price") if entry else event.get("price") or 0)
        item = queue_upsell(store, row, title=title, price=price,
                            currency=row["currency"], event_id=event_id)
        return {"ok": True, "res_ref": res_ref, "kind": "upsell_queued", "item": item.id}

    return {"ok": False, "res_ref": res_ref, "event_id": event_id,
           "note": f"unknown event kind '{kind}'"}


# --------------------------------------------------------------------------
# send dispatch - tools/review.py send calls this for every claimed item
# --------------------------------------------------------------------------
def send_item(settings: Settings, store: Store, item: Item) -> dict:
    """Perform the one real, guarded action a queued item stands for.

    `WriteBlocked` and adapter errors propagate to the caller unchanged
    (`tools/review.py` catches both, exactly like the reference agent).
    """
    if item.kind in ("checkin_invite", "checkin_doorcode"):
        return _send_message(settings, store, item)
    if item.kind == "checkin_upsell":
        return _send_upsell(settings, store, item)
    raise ValueError(f"no send handler for kind '{item.kind}'")


def _send_message(settings: Settings, store: Store, item: Item) -> dict:
    email = get_email(settings)
    draft = item.draft or {}
    payload = item.payload or {}
    result = email.send(payload.get("email") or "", draft.get("subject", ""),
                        draft.get("body", ""), item=item)
    res_ref = payload.get("res_ref", "")
    if item.kind == "checkin_invite":
        event_kind = "invite_sent" if payload.get("message_kind") == "invite" else "follow_up"
        store_ext.record_checkin_event(store, f"{item.id}:sent", res_ref, event_kind,
                                       detail=f"sent to {payload.get('email', '')}")
    return result


def _send_upsell(settings: Settings, store: Store, item: Item) -> dict:
    pms = get_pms(settings)
    payload = item.payload or {}
    label = money(float(payload.get("price", 0)), payload.get("currency", "EUR"))
    result = pms.add_note(payload.get("res_ref", ""),
                          f"Upsell charged: {payload.get('title', '')} - {label}", item=item)
    store_ext.add_upsell(store, payload.get("res_ref", ""), float(payload.get("price", 0)))
    store_ext.record_checkin_event(store, f"{item.id}:folio", payload.get("res_ref", ""),
                                   "folio_charge", detail=payload.get("title", ""),
                                   amount=float(payload.get("price", 0)))
    return result
