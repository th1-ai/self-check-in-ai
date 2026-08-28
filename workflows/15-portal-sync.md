# Workflow: portal sync - reading back what the guest did

Objective: apply every new check-in portal event (ID result, waiver, payment,
an upsell tap) to the PMS and the review queue, and see what needs a person.

This is the "hands that guest to the front desk" half of the promise
(`cant`). Read `docs/how-it-works.md` "The portal sync leg" before touching
this workflow - the source demo this repo was built from never implemented
the failure path at all; this repo does.

## Inputs

- `data/imports/checkin_portal_events.jsonl` - newline-delimited JSON, one
  completion event per line, appended by your own check-in portal or a
  webhook relay. See `docs/integrations.md#guest-check-in-portal` - this
  repo does not build the portal itself.
- The guest must already be tracked (`workflows/10-checkin-sweep.md` has run
  at least once for that reservation) - an event for an unknown reservation
  is reported, not silently dropped or crashed on.

## Steps

1. **Run one pass.**
   ```bash
   python3 tools/portal_sync.py --once
   python3 tools/portal_sync.py --once --dry-run
   ```
   Only lines appended since the last pass are read (a `kv` cursor,
   `portal_sync:file_offset`) - `--reset-cursor` re-reads the whole file.

2. **Four event kinds, each deterministic:**
   - `id_check` - a name match writes `id_status: verified` and advances the
     guest to the waiver step; a mismatch writes `id_status: failed` and
     escalates.
   - `waiver` - signed advances to payment; declined escalates.
   - `payment` - `authorized` (prepaid) or `charged` (balance-due) completes
     the check-in and posts a PMS folio note; anything else escalates. A
     completed, smart-lock guest arriving **today** gets a computed door code
     and a queued notice - see step 4.
   - `upsell` - a confirmed tap queues a `checkin_upsell` item (a PMS folio
     note once approved); an unconfirmed one is ignored.

3. **See what needs a person.**
   ```bash
   python3 tools/review.py list --status needs_human --kind checkin_escalation
   ```
   Each escalation names the reservation, the guest, and exactly why (a name
   mismatch, a declined card, an unsigned waiver). There is no draft to
   approve here - resolve it in person (verify the ID at the desk, call the
   guest about the card), then close it:
   ```bash
   python3 tools/review.py reject <id> --reason "resolved: <what you did>"
   ```

4. **Door codes.** A code is computed the moment a smart-lock guest arriving
   today completes check-in (`tools/sweep.py:door_code_for`) and a guest
   notice is queued like any other message - `workflows/80-review.md`
   approves and sends it. **This repo cannot program a real lock** -
   `systems.locks` is a stub; see `docs/how-it-works.md` "Door codes: honest
   about hardware" and `knowledge/checkin-policy.md` for the vendors you
   would need to wire up.

5. **Keep it running.** `make schedule ARGS="--all"` includes this job
   (`config/agent.yaml: schedule.portal_sync`, every 15 minutes by default -
   guests expect a prompt door code, so this runs more often than the sweep).

## Edge cases

- **An event for a reservation nobody has invited yet.** Reported as
  `"unknown reservation - run the sweep first"`, not applied. Run
  `workflows/10-checkin-sweep.md` for that arrival, then re-run this job.
- **Two events for the same `event_id`.** `checkin_events.event_id` is
  unique; a re-read of the same line (a cursor reset, a portal retry) is a
  no-op the second time.
- **A non-smart-lock guest completes check-in.** No door code is computed or
  queued - `door_code` stays `None` on that guest's row.
