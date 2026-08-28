# Workflow: first-run setup

Objective: get Self Check-In AI from a fresh clone to a working demo, then to
real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never overwrites
   your own copies). `make doctor` will show a `FAIL` on "hotel identity"
   right after setup - that is expected, it means the property name is still
   the shipped placeholder ("Hotel Aurora"). Everything else should be `ok`
   or `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see 3 invites and 3 follow-ups drafted, 4 stays skipped with a
   reason each, then the portal sync leg process 7 sample completion events -
   including two escalations to the front desk and the door-code showcase
   guest. The line `DEMO OK — 13 items processed, 8 drafted, 0 sent (shadow)`
   is the pass signal. If you do not see that, stop and read
   `workflows/99-troubleshooting.md` before going further.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address, contact,
   languages, currency). Then:
   ```bash
   cp knowledge/property.example.md      knowledge/property.md
   cp knowledge/faq.example.md           knowledge/faq.md
   cp knowledge/checkin-policy.example.md knowledge/checkin-policy.md
   cp knowledge/signature.example.md     knowledge/signature.md
   ```
   Replace the placeholder content with the real property's facts, and read
   `knowledge/checkin-policy.md` in full - it covers your local ID-check law,
   the waiver text, and which lock vendor you would need to wire up if you
   want real door codes. See `knowledge/README.md` for how to write it well.

4. **Set the check-in windows and rules.** `config/agent.yaml`'s `windows:`
   block (invite at D-30, chase from D-5, one nudge per 48 hours, room ready
   at 14:00) and `rules:` block (the six on/off switches) ship with sensible
   defaults from the source this was built from. Change them once you have
   watched a few real passes - `docs/how-it-works.md` explains each one.

5. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider` starts
   as `interactive` - it asks you, in this Claude Code session, instead of
   calling a model. That costs nothing extra and is the best way to see how
   Self Check-In AI writes the invite and door-code messages.
   `docs/how-it-works.md` and `docs/safety.md` explain the other three
   providers (`mock`, `claude-code`, `anthropic`) and when to move to one.

6. **Connect a real PMS (optional for now).** `systems.pms.adapter` in
   `config/hotel.yaml` starts as `mock`, which only ever sees the 10 fixture
   reservations. `docs/integrations.md` covers `csv` (works with any PMS) and
   `cloudbeds`. Run `make doctor` after changing it.

7. **Decide where portal events come from.** This repo does not build the
   guest-facing check-in portal itself - see `docs/how-it-works.md` "Scope"
   and `docs/integrations.md#guest-check-in-portal`. Point your own portal
   (or a webhook relay) at `data/imports/checkin_portal_events.jsonl`.

8. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real and `knowledge/property.md` +
   `knowledge/checkin-policy.md` exist, the "hotel identity" and
   "check-in policy" lines turn green. Move on to
   `workflows/10-checkin-sweep.md` to run the sweep for real.
