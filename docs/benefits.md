# The business case

## Why it matters (from the roster)

Check-in admin is pure queue time, and the minutes after a completed
check-in are the highest-intent upsell moment a hotel gets. Doing both
digitally shortens the line for everyone - or removes the desk entirely for
self-service properties and STR portfolios - while ancillary revenue books
itself.

## What to expect (from the roster)

Guests arrive already checked in: ID verified, card on file, waiver signed,
folio updated, door code issued and revoked on schedule - and a stream of
pre-paid upsells attached before they ever reach the lobby.

**ROI target: -70% front-desk queue time (guest-facing).**

## What to measure

`python3 tools/report.py` (`make report`) reads straight from
`checkin_guests` and `checkin_events` - nothing here is asserted:

- **Arrivals in the invite window** - the pipeline this agent is working.
- **Checked in online (%)** - completed check-ins over arrivals due.
- **Awaiting completion** - guests mid-portal right now (a leading indicator
  of tomorrow's desk queue).
- **Portal upsell revenue** - only counts a `folio_charge` event that
  actually got approved and posted (`checkin_upsell` items go through the
  same review queue as a guest email, by design - see `docs/safety.md`).
- **Desk minutes saved** - completed check-ins x `windows.desk_minutes_saved`
  (default 6, edit it if your own front desk timed something different).
- **Escalations to the front desk** - every name mismatch, declined card and
  unsigned waiver this agent caught and handed over, instead of guessing.

## Honest caveats

- **The -70% figure and "desk minutes saved" are a planning estimate, not a
  measurement of your property.** `windows.desk_minutes_saved` is a single
  configurable number, the same shape as the source this repo was built
  from. If you want a real number, time your own desk before and after.
- **This repo does not build the guest-facing portal.** The upsell revenue,
  the online check-in rate and the desk-minutes saved all depend on a real
  portal being wired up and actually used - see
  `docs/how-it-works.md` "Scope" and `docs/integrations.md#guest-check-in-portal`.
  Until then, `make demo`'s numbers are illustrative, not a forecast.
- **Door codes need a real lock system to mean anything operationally.**
  `systems.locks` is a stub - see `docs/integrations.md#locks`. Without a
  vendor wired up, "door code issued" means a code was computed and a
  message was drafted, not that the door will actually open with it.
- **Upsell revenue here does not merge with any other agent's ancillary
  revenue.** If you also run a journey/upsell agent against the same PMS,
  `checkin_guests.upsells_total` is this agent's own ledger - see
  `docs/how-it-works.md` design decision 5.
