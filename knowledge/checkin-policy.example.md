# Check-in policy - Hotel Aurora

<!--
Copy this to knowledge/checkin-policy.md and replace everything with your
own property's rules. tools/checkin.py and prompts/invite.md both read this
file (via the knowledge: frontmatter list). See knowledge/README.md.
-->

## Local ID-check law

- Country: replace with yours. Some countries require an in-person ID check
  at check-in by law (police registration, tourist tax registers). If yours
  does, turn `rules.require-id` OFF in `config/agent.yaml` - the portal still
  captures everything else and leaves the ID step to staff on arrival. Do not
  rely on this file to enforce the law; check with your own lawyer.
- Where an in-person check is not required, the portal's ID photo plus the
  booking-name match is the check.

## Registration card and damage waiver - what the guest agrees to

- Registration card: guest name, arrival and departure dates, room type,
  number of guests, and a statement that the details on file are correct.
- Damage waiver (one short paragraph, in the guest's own words is best):
  *"I agree to pay for any damage I cause to the room or its contents beyond
  normal wear, at Hotel Aurora's cost to repair or replace."*
- Keep this to one paragraph. A long waiver does not get read; a short one
  does.

## Payment

- Prepaid stays: a EUR 0 card check only, to confirm the card on file works.
  Nothing is charged at check-in.
- Balance-due stays: the remaining room balance plus city tax, both shown
  before the guest is asked to pay.
- City tax: replace with your own rate and rule (per person per night, a flat
  fee, exemptions for children). Hotel Aurora charges EUR 1.50 per adult
  per night, waived under 12.

## The upsell catalogue

See `config/agent.yaml: upsells` for the list `tools/portal_sync.py` reads.
Edit prices and items there; this file is just the guest-facing description
each item's `title` should match.

## Door codes: what this agent can and cannot do

This agent computes a stable door code for a smart-lock room and drafts the
guest's activation message. **It cannot program a real lock**  -
`systems.locks` is a stub in every repo in this family (see
`docs/integrations.md#locks`). Two ways to close that gap:

1. **Wire a real lock vendor.** Name yours here so your Claude Code session
   knows which adapter to build: `<your lock system, e.g. Salto KS / ASSA
   ABLOY Visionline / dormakaba / Akiles / Nuki / RemoteLock / TTLock /
   Igloohome>`. `docs/integrations.md#implement-your-own` has the recipe.
2. **Have a person apply the code by hand.** Until the adapter exists, every
   computed code becomes a queued `checkin_doorcode` item
   (`workflows/80-review.md`) - approve it, then program the code into your
   lock system's own console and send the guest message.

### No front desk at some hours: the interim manual procedure

Option 2 above assumes someone is on site, at a console, when the item is
approved. If your property has no staff present for part of the day (a
night audit gap, an unstaffed aparthotel), decide - and write down here -
who covers a late arrival before you go live with real codes:

- **Name a person and a channel.** `<e.g. the duty manager, reachable on
  WhatsApp, checks the queue from home for arrivals after 18:00>`. Put the
  same contact in `contacts.manager` / `contacts.escalation_email` in
  `config/hotel.yaml` so `make doctor` and any `systems.messaging` alert
  reach them.
- **Decide how they program the code remotely**, if your lock vendor
  supports it (a vendor app, a cloud console) - or whether a printed
  emergency-access code / lockbox is the real fallback until a vendor
  adapter exists. Write the actual answer here; do not leave this as "figure
  it out later" - see SIMULATION.md finding 5 for why a first-time reader
  needs this spelled out, not implied.
- **Until that person and channel are named, do not turn `door-code-auto`
  on for real guests outside staffed hours.** `rules.door-code-auto` in
  `config/agent.yaml` can be turned off entirely, or you can simply hold
  every `checkin_doorcode` item unapproved until someone is on site -
  `workflows/90-go-live.md`'s checklist covers this.

## Escalation contacts

- Front desk / on-call: fill in how staff are notified today (a phone, a
  WhatsApp group, a shift handover board). `systems.messaging` can post
  automatically once configured - see `docs/integrations.md`.
- A guest with a failed ID match, a failed card, a declined waiver, or a
  lockout outside the digital path always goes here - see `docs/safety.md`
  "What the agent will not do."
