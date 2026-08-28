# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

## Status

### PMS - `systems.pms.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/hotel/*.json`. What `make demo` uses. |
| `csv` | universal | a CSV export | Reads `data/imports/*.csv`. **Start here.** Works with every PMS. |
| `cloudbeds` | built | OAuth app + refresh token | Live reads and writes. |
| `cli` | universal | a JSON-speaking CLI | Advanced. Bridges to a vendor command line tool. |

**There is no Mews adapter** (nor Opera, RoomRaccoon, Little Hotelier, or any
other specific PMS not in the table above) - only `mock`, `cloudbeds` and
`cli` are built adapters. If that is your PMS, use `csv`: export a
reservations report on whatever schedule your PMS supports and drop it in
`data/imports/`. It is a real, working integration, just not a live one -
see "In CSV mode" below for exactly what that means. Ask this Claude session
to build a live adapter for your PMS once you know you want one -
`docs/integrations.md#implement-your-own` has the recipe.

**`csv` - the one that always works.** Export from your PMS and drop the files in
`data/imports/`:

- `reservations.csv` - `id, status, check_in, check_out, room_type_id,
  room_type_name, room_id, adults, children, source, total, balance, currency,
  guest_email, guest_first_name, guest_last_name, guest_phone, guest_country,
  guest_language`. `guest_language` is a two-letter code (`en`, `nl`, ...);
  leave it blank if your PMS export does not carry it - see "No language on
  the booking" below.
- `guests.csv` - `id, first_name, last_name, email, phone, country, language, vip`
- `rooms.csv` - `id, name, max_occupancy, count, rank`
- `rates.csv` - `date, room_type_id, price, currency, min_los, available, closed`

Headers are matched loosely: `checkIn`, `check_in` and `Check In` all work, and
extra columns are kept. Dates must be `YYYY-MM-DD`. Only `reservations.csv` is
required; the rest add capability.

**Extra columns this agent reads that are not in the core schema above.**
`smart_lock` (`1`/`true`/`yes`/`y`, case-insensitive = smart-lock room;
`0`/`false`/`no`/blank = not - a real truthy check, not "any non-empty
string"), `payment_kind` (`balance_due` or `prepaid`) and `city_tax` (a
number, in `hotel.currency`) all pass through
via the CSV adapter's generic `extra=row` column and are read directly by
`tools/checkin.py`. Add them to your `reservations.csv` export as plain
columns; there is nothing else to configure.

**No language on the booking vs. an unsupported language - not the same
thing.** No `guest_language` value at all means "we do not know" - the agent
drafts in `hotel.default_language` and says nothing more; that is an honest
default, not an error. A `guest_language` VALUE that is not one of
`hotel.languages` is different: the draft still falls back to
`hotel.default_language`, but the item is flagged `needs_human` with the
reason ("guest's language is `<code>`, not in `hotel.languages`") so a person
sees it - never silently treated the same as "unknown." See
`tools/checkin.py:needs_human_for_message()`.

In CSV mode the agent cannot write back to your PMS, so anything it wants to
change is appended to `data/exports/pms_writes.csv` with everything a person
needs to apply it by hand. That is a feature: it is how you check the agent's
judgement before you give it write access.

**`cloudbeds`.** Create an app in the Cloudbeds developer portal, authorise it
once against your property, and put the result in `.env`:

```
CLOUDBEDS_CLIENT_ID=
CLOUDBEDS_CLIENT_SECRET=
CLOUDBEDS_REFRESH_TOKEN=
CLOUDBEDS_PROPERTY_ID=
```

Scopes: `read:reservation`, `write:reservation`, `read:guest`, `read:room`,
`read:rate`, `write:rate`, `read:hotel`. The access token refreshes itself.

**`cli`.** If your PMS already has a command line tool that prints JSON, point at
it. See the profiles at the top of `core/adapters/pms_cli.py`.

### Guest check-in portal - not a `systems.*` adapter

<a id="guest-check-in-portal"></a>

| Source | Status | Needs | Notes |
|---|---|---|---|
| Your own portal / a webhook relay | universal | `data/imports/checkin_portal_events.jsonl` | `tools/portal_sync.py` reads new lines; a cursor means each pass only reads what is new. |

This repo does not build the guest-facing check-in portal (the ID photo, the
signature pad, the card sheet, the upsell taps) - see
`docs/how-it-works.md` "Scope". It expects one newline-delimited JSON object
per completion event, appended (never edited) to
`data/imports/checkin_portal_events.jsonl`:

```json
{"event_id": "portal-8841", "res_ref": "MH-3001", "kind": "id_check", "id_match": true, "id_name": "Lena Novak", "at": "2026-09-03T10:12:00+00:00"}
{"event_id": "portal-8842", "res_ref": "MH-3001", "kind": "waiver", "signed": true, "at": "2026-09-03T10:13:00+00:00"}
{"event_id": "portal-8843", "res_ref": "MH-3001", "kind": "payment", "result": "charged", "amount": 246.0, "at": "2026-09-03T10:14:00+00:00"}
{"event_id": "portal-8844", "res_ref": "MH-3001", "kind": "upsell", "slug": "wine-in-room", "confirmed": true, "at": "2026-09-03T10:20:00+00:00"}
```

`event_id` must be unique per event - it is how a re-read of the same line
(a cursor reset, a portal retry) is ignored the second time. If you want a
real portal built, ask your Claude Code session to design one against
`tools/portal_sync.py:apply_event()`'s four event kinds, or ask TH1.

### Email - `systems.email.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/*.eml` and `*.json`. |
| `imap` | universal | mailbox + app password | Any provider. **Start here.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

This agent never reads guest email in - it only sends. `systems.email` is
still used for `email.send()` (invites, follow-ups, door-code notices).
`make doctor`'s "sample messages" count for the mock adapter includes the
portal-event fixtures in `fixtures/inbound/` too (the same folder, a
different purpose) - harmless, since nothing here calls `fetch_unread()`.

**`imap`.** In `.env`:

```
EMAIL_ADDRESS=reservations@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587              # 587 STARTTLS, 465 implicit TLS
```

Google, Microsoft and Fastmail all issue app-specific passwords. Two-factor stays
on and you can revoke the password without touching the account.

Replies carry `In-Reply-To` and `References`, so they land inside the guest's
existing thread rather than starting a new one.

**`gmail`.** Google Cloud Console: enable the Gmail API, configure the consent
screen, create an OAuth client of type **Desktop app**, download the JSON to
`credentials.json`. Then `pip install google-api-python-client google-auth-oauthlib`
and run `make doctor`; a browser opens once and writes `token.json`. Scopes:
`gmail.readonly`, `gmail.send`, `gmail.modify`.

### Messaging - `systems.messaging.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/messages.json`. |
| `unipile` | built | your own UniPile account | WhatsApp on your own number. |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, or your own endpoint. |

**`unipile`.** You create the account, you connect your number by QR code, you
own the credentials: `UNIPILE_DSN`, `UNIPILE_API_KEY`, `UNIPILE_ACCOUNT_ID`.
WhatsApp Business policy limits what you may send outside a guest-initiated
window; read your provider's rules before turning this on.

**`webhook`.** The simplest possible outbound: set `MESSAGING_WEBHOOK_URL` and
the agent POSTs `{chat_id, text, kind, hotel, sent_at}`. Your automation tool
delivers it however you like. Send-only.

### Sheets - `systems.sheets.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Writes `data/exports/<sheet>.csv`. |
| `google` | built | service account JSON | A live shared spreadsheet. |

For `google`: enable the Sheets API, create a service account and a JSON key,
save it as `service_account.json`, and share your spreadsheet with the service
account's email address as an Editor. Set `systems.sheets.spreadsheet_id` to the
long id from the sheet's URL.

### Locks - `core.adapters.get_stub("locks", settings)`

<a id="locks"></a>

**Stub.** `tools/portal_sync.py` computes a real door code
(`tools/sweep.py:door_code_for`) and always attempts `locks.issue_key()` -
today that call is always blocked or unimplemented (see
`docs/how-it-works.md` "Door codes: honest about hardware"), so the
computed code becomes a queued guest message instead, and someone programs
it into your lock system by hand or through the adapter you build. Named
systems a hotel would need to wire up here: **Salto KS**, **ASSA ABLOY
Visionline/Vostio**, **dormakaba Community/Ambiance**, **Akiles**, **Nuki**,
**RemoteLock**, **TTLock**, **Igloohome**. Copy `core/adapters/pms_csv.py` as
the shape, implement `issue_key` and `list_keys` first (the two methods
`core/adapters/base.py:Locks` defines), and register it under `STUB_SYSTEMS`
handling in `core/adapters/__init__.py:get_stub` - or just ask your Claude
Code session, using the recipe below.

### Everything else

`pos`, `accounting`, `reviews`, `calendar`, `payments` and `procurement` are
**stubs** too: the interface exists, nothing is implemented. Calling one
raises an error that tells you exactly this. If your agent needs one, use
the recipe below.

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose, and your Claude Code session can do this with
you in an afternoon. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need a PMS adapter for **<your system>**. Its API docs are at **<url>** and
> I have credentials in `.env` as `<VAR names>`. Copy `core/adapters/pms_csv.py`
> as the shape, implement `ping`, `capabilities` and the read methods first,
> register it in `core/adapters/__init__.py`, and stop before the write methods
> so I can check the reads with `make doctor`.

### The five steps

**1. Copy the closest existing adapter.**
`core/adapters/pms_csv.py` for a PMS, `email_imap.py` for a mailbox,
`messaging_webhook.py` for a chat channel. They are short and heavily commented.

**2. Implement `ping()` and `capabilities()` first.**

```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```

`make doctor` reads both. Getting them right first means the rest of the work has
a feedback loop.

**3. Implement the reads.** Map the vendor's fields onto the dataclasses in
`core/adapters/base.py` (`Reservation`, `Guest`, `RoomType`, `RateRow`,
`EmailMessage`, `ChatMessage`). Put anything you do not map into `.extra` rather
than dropping it. Dates are ISO `YYYY-MM-DD`. Money is a float in the hotel's
currency.

**4. Implement the writes, each with the guard.**

```python
from core.adapters.base import guarded_write

@guarded_write("pms_write")
def add_note(self, reservation_id: str, text: str) -> dict:
    ...
```

The decorator is not optional. Without it your adapter can write while the agent
is in shadow mode, which defeats the entire safety model. The action name should
be one of the values in `review.require_approval_for`.

**5. Register it.** One line in `core/adapters/__init__.py`:

```python
REGISTRY["pms"]["yoursystem"] = "core.adapters.pms_yoursystem:YourSystemPMS"
```

Then set `systems.pms.adapter: yoursystem` in `config/hotel.yaml` and run
`make doctor`.

### Rules that matter

- **`ping()` never raises.** It returns `HealthCheck(ok=False, ...)` with a hint.
  A broken adapter must still produce a readable doctor table.
- **Every write is decorated.** No exceptions.
- **Rate limits belong in the adapter.** Use `core/adapters/_http.py:RateLimiter`.
  Retry 429 and 5xx with backoff; never retry a 4xx.
- **Never log a credential.** `core/log.py` masks anything whose key looks like a
  secret, but do not rely on it.
- **Redact on ingestion.** Any guest-written text goes through
  `core.redact.redact()` before it is stored or shown to a model.
- **Write a test.** Copy `tests/test_core_adapters_mock_csv.py`. It should run
  with no network: feed your parser a fixture, check the dataclass that comes out.

### `core/` is shared

`core/` is identical in all 28 agents in this family. If you change something in
`core/`, keep it generic - a hotel-specific tweak belongs in `tools/` or in your
own adapter file, not in the shared runtime.
