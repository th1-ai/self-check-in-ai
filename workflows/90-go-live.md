# Workflow: shadow to live

Objective: decide, together with the hotel, whether Self Check-In AI is
ready to send approved invites, follow-ups and door-code notices - and post
approved upsells to the folio - on its own instead of only drafting them.
Make the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly
what changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name, address and contact
      details, and `knowledge/property.md`, `knowledge/faq.md` and
      `knowledge/checkin-policy.md` exist and are accurate (not the shipped
      examples) - the ID-check law and the waiver text specifically.
- [ ] At least a few days of real `make run` / `python3 tools/portal_sync.py`
      passes have gone through the review queue, not just the demo fixtures.
- [ ] The hotel has read and edited enough invite/follow-up drafts to trust
      the language and tone for the languages you actually serve.
- [ ] Every escalation from a real pass was actually resolved in person and
      closed (`python3 tools/review.py reject <id> --reason "resolved: ..."`)
      - not silently ignored.
- [ ] The hotel has decided on, and added, the AI-disclosure line to
      `knowledge/signature.md` (`docs/safety.md` has suggested wording and the
      EU AI Act Article 50 context).
- [ ] A real PMS is connected (`systems.pms.adapter: csv` or `cloudbeds`) and
      `make doctor` shows it healthy - going live on the `mock` adapter would
      only ever touch the fixtures.
- [ ] If door codes matter to this property: a real lock system is wired up
      (`docs/integrations.md#locks`) or the hotel has agreed a manual process
      for applying the computed code - see `knowledge/checkin-policy.md`. If
      the property has no staff on site for part of the day, this is not
      optional: a named person and channel for a late arrival's code during
      unstaffed hours must exist BEFORE `door-code-auto` runs live - see
      "No front desk at some hours" in `knowledge/checkin-policy.md`.
- [ ] `python3 tools/review.py stale` has been run once, right before the
      flip, to clear anything queued during shadow testing.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `send_email` and `pms_write` by
   default - it should. Going live means **approved items get sent or
   posted**, not that Self Check-In AI starts acting on its own. There is no
   config that changes that for a `checkin_escalation`.
3. Run `make doctor` again to confirm.
4. Run one real pass and manually watch a send go through:
   ```bash
   make run ARGS="--limit 1"
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
5. Tell the hotel exactly what just changed: an approved invite, follow-up or
   door-code notice now actually leaves the mailbox, and an approved upsell
   now actually posts to the PMS folio, the next time someone (or a
   scheduled job) runs `python3 tools/review.py send` - it is still never
   automatic before that approval, and every escalation still waits for a
   person.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action and every PMS write on the next pass, mid-
schedule, with no other change required.
