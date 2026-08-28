# Workflow: working the review queue

Objective: turn a queued item into a decision - approve, edit, or reject -
and, once approved, actually send or post it.

Nothing reaches a guest, the PMS folio, or (once you wire one) a real lock
without going through this. `mode: shadow` blocks every guarded action for
everything except an item you have approved or edited; see `docs/safety.md`
for the full guard.

## The four kinds of item

| `kind` | What it is | The send action |
|---|---|---|
| `checkin_invite` | A drafted invite or follow-up email | `email.send` |
| `checkin_doorcode` | A door-code notice for an eligible smart-lock guest | `email.send` |
| `checkin_upsell` | A confirmed upsell to post to the PMS folio | `pms.add_note` |
| `checkin_escalation` | A name mismatch, a declined card, an unsigned waiver | none - a real-world action |

## Steps

1. **See what is waiting.**
   ```bash
   make review
   python3 tools/review.py list --status needs_human --kind checkin_escalation
   ```
   Each line shows the item id, its status, its kind, and a short label
   (guest name, or the upsell title).

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   This prints the original context, the draft (for `checkin_invite` /
   `checkin_doorcode`), and the full event history. Summarise it for the
   hotel in plain language - who this is, what Self Check-In AI drafted, why
   it needs a look - do not paste the raw JSON at them.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
   python3 tools/review.py reject <id> --reason "wrong tone"
   ```
   `edit` records the before/after pair as a `learnings` row - there is no
   coach layer in this repo to act on it automatically (`coach: no` in the
   brief), but the record is there if you add one later.

   **`checkin_escalation` items have no draft to approve.** Resolve them in
   person, then close with `reject --reason "resolved: ..."` - see
   `workflows/15-portal-sync.md`.

4. **Send or post what was approved.**
   ```bash
   python3 tools/review.py send
   ```
   This claims everything `approved`/`edited`, dispatches by `kind`
   (`tools/checkin.py:send_item`), and records the result. In `mode: shadow`
   this only works for an item you just approved (that is the one case
   shadow mode lets through - see `core/review.py`); nothing else can ever
   be sent while shadow is on.

5. **A failed send.** `send` marks the item `failed` with the error attached.
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it for another attempt after you have fixed the cause (usually a
   mailbox or PMS credential - `make doctor` will say which).

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- A guest outside `hotel.languages` is always `needs_human` - never approve
  one of these without checking the drafted English fallback reads well.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
