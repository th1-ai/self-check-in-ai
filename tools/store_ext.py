"""tools/store_ext.py - Self Check-In AI's own tables, layered on core.store.Store.

The generic `items` table (core/store.py) is the review queue: a drafted
invite, a follow-up, a door-code notice or an upsell folio post are all rows
there, told apart by `kind`. This module adds the guest tracking state that
does not fit a queue row - one row per reservation, carrying the mutable
check-in state (invite_status, portal_step, id/waiver/card status, the door
code) - plus the append-only `checkin_events` audit feed and the pure helper
functions the engines and the tests share.

Call :func:`ensure_schema` once per `Store` before touching either table -
every tool in this repo does it right after constructing its `Store`. Nothing
here replaces `core.store` - additive, same connection (`store.db`), same
`utcnow()` convention, same JSON-column convention.
"""

from __future__ import annotations

from typing import Any

from core.store import Store, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkin_guests (
  res_ref             TEXT PRIMARY KEY,
  guest_name          TEXT NOT NULL,
  first_name          TEXT NOT NULL DEFAULT '',
  email               TEXT,
  phone               TEXT,
  language            TEXT,
  party               INTEGER NOT NULL DEFAULT 1,
  room_type           TEXT,
  room_number         TEXT,
  nights              INTEGER NOT NULL DEFAULT 1,
  check_in            TEXT NOT NULL,
  check_out           TEXT,
  channel             TEXT,
  vip                 INTEGER NOT NULL DEFAULT 0,
  smart_lock          INTEGER NOT NULL DEFAULT 0,
  payment_kind        TEXT NOT NULL DEFAULT 'balance_due',
  balance             REAL NOT NULL DEFAULT 0,
  city_tax            REAL NOT NULL DEFAULT 0,
  currency            TEXT NOT NULL DEFAULT 'EUR',
  invite_status       TEXT NOT NULL DEFAULT 'not_sent',
  invited_at          TEXT,
  portal_step         TEXT NOT NULL DEFAULT 'none',
  id_status           TEXT NOT NULL DEFAULT 'pending',
  waiver_signed       INTEGER NOT NULL DEFAULT 0,
  card_status         TEXT NOT NULL DEFAULT 'none',
  door_code           TEXT,
  door_code_issued_at TEXT,
  upsells_total       REAL NOT NULL DEFAULT 0,
  seeded              INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkin_guests_status
  ON checkin_guests (invite_status, portal_step);

CREATE TABLE IF NOT EXISTS checkin_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id    TEXT UNIQUE,
  res_ref     TEXT NOT NULL,
  kind        TEXT NOT NULL,
  detail      TEXT,
  amount      REAL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkin_events_res ON checkin_events (res_ref, created_at);
"""

# The columns a fresh row seeds from the PMS/fixture the first time we see a
# reservation. Once the row exists these are the ONLY columns a re-sync may
# touch - invite_status, portal_step and everything past it belong to this
# agent from then on, never to the PMS (see docs/how-it-works.md).
_PMS_COLUMNS = (
    "guest_name", "first_name", "email", "phone", "language", "party",
    "room_type", "room_number", "nights", "check_in", "check_out", "channel",
    "vip", "smart_lock", "payment_kind", "balance", "city_tax", "currency",
)
# The mutable columns a fresh row may seed from `extra` (fixtures can pre-seed
# a guest as already invited/completed - see fixtures/hotel/reservations.json).
_SEED_COLUMNS = (
    "invite_status", "invited_at", "portal_step", "id_status", "waiver_signed",
    "card_status", "door_code", "door_code_issued_at", "upsells_total",
)


def ensure_schema(store: Store) -> None:
    store.migrate(SCHEMA)


# -- checkin_guests -----------------------------------------------------------
def sync_guest(store: Store, res_ref: str, pms_fields: dict, *,
              seed: dict | None = None) -> dict:
    """Insert-or-refresh the tracking row for one reservation.

    A fresh row is seeded from `pms_fields` (the read-only facts: name, room,
    dates, money) and, once only, from `seed` (fixtures pre-populate a guest
    as already invited/completed so the demo shows every stage - see
    docs/how-it-works.md). An existing row only has its `_PMS_COLUMNS`
    refreshed; everything this agent owns (invite_status, portal_step, ...)
    is left untouched, exactly like `core.store.Store.upsert_item`'s payload
    refresh.
    """
    row = get_guest(store, res_ref)
    now = utcnow()
    if row is None:
        seed = {k: v for k, v in (seed or {}).items() if k in _SEED_COLUMNS}
        cols = ["res_ref", *_PMS_COLUMNS, *seed.keys(), "seeded", "created_at", "updated_at"]
        values = [res_ref]
        values += [pms_fields.get(c) for c in _PMS_COLUMNS]
        values += list(seed.values())
        values += [int(bool(seed)), now, now]
        placeholders = ",".join("?" * len(cols))
        store.db.execute(
            f"INSERT INTO checkin_guests ({', '.join(cols)}) VALUES ({placeholders})",
            values)
        return get_guest(store, res_ref)  # type: ignore[return-value]
    sets = ", ".join(f"{c}=?" for c in _PMS_COLUMNS)
    store.db.execute(
        f"UPDATE checkin_guests SET {sets}, updated_at=? WHERE res_ref=?",
        [pms_fields.get(c) for c in _PMS_COLUMNS] + [now, res_ref])
    return get_guest(store, res_ref)  # type: ignore[return-value]


def get_guest(store: Store, res_ref: str) -> dict | None:
    row = store.db.execute("SELECT * FROM checkin_guests WHERE res_ref=?", (res_ref,)).fetchone()
    return dict(row) if row else None


def list_guests(store: Store, *, invite_status: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM checkin_guests", []
    if invite_status:
        sql += " WHERE invite_status=?"
        params.append(invite_status)
    sql += " ORDER BY check_in ASC"
    return [dict(r) for r in store.db.execute(sql, params).fetchall()]


def set_invite_sent(store: Store, res_ref: str, status: str, when: str) -> None:
    """`status` is `invited` (first touch) or `reminded` (a follow-up)."""
    store.db.execute(
        "UPDATE checkin_guests SET invite_status=?, invited_at=?, updated_at=? WHERE res_ref=?",
        (status, when, utcnow(), res_ref))


def set_portal_fields(store: Store, res_ref: str, **fields: Any) -> None:
    allowed = {"portal_step", "id_status", "waiver_signed", "card_status",
              "door_code", "door_code_issued_at"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"set_portal_fields cannot write {sorted(bad)}")
    cols = ", ".join(f"{k}=?" for k in fields)
    params = [int(v) if isinstance(v, bool) else v for v in fields.values()]
    store.db.execute(f"UPDATE checkin_guests SET {cols}, updated_at=? WHERE res_ref=?",
                     [*params, utcnow(), res_ref])


def add_upsell(store: Store, res_ref: str, amount: float) -> None:
    store.db.execute(
        "UPDATE checkin_guests SET upsells_total = upsells_total + ?, updated_at=? "
        "WHERE res_ref=?", (float(amount), utcnow(), res_ref))


# -- checkin_events (append-only PMS-facing feed) -----------------------------
def record_checkin_event(store: Store, event_id: str, res_ref: str, kind: str, *,
                         detail: str = "", amount: float | None = None) -> bool:
    """Append one audit row. Returns False (no-op) if `event_id` was seen before."""
    existing = store.db.execute(
        "SELECT id FROM checkin_events WHERE event_id=?", (event_id,)).fetchone()
    if existing is not None:
        return False
    store.db.execute(
        "INSERT INTO checkin_events (event_id, res_ref, kind, detail, amount, created_at) "
        "VALUES (?,?,?,?,?,?)", (event_id, res_ref, kind, detail, amount, utcnow()))
    return True


def list_checkin_events(store: Store, res_ref: str | None = None, *, limit: int = 200) -> list[dict]:
    sql, params = "SELECT * FROM checkin_events", []
    if res_ref:
        sql += " WHERE res_ref=?"
        params.append(res_ref)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    return [dict(r) for r in store.db.execute(sql, params).fetchall()]
