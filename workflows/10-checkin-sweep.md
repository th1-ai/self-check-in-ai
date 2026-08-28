# Workflow: the pre-arrival sweep

Objective: run one pass over every upcoming arrival and see what Self
Check-In AI decided - who gets a link, who gets chased, and who was
deliberately left alone.

## Inputs

- A configured `systems.pms.adapter` (`mock` by default - see
  `workflows/00-setup.md` step 6 to connect a real PMS).
- `config/agent.yaml`'s `windows:` and `rules:` blocks - the defaults work;
  change them once you have watched a few real passes.

## Steps

1. **Run one pass.**
   ```bash
   make run
   make run ARGS="--dry-run"       # compute everything, write nothing
   make run ARGS="--provider mock" # rehearse without spending a model call
   ```
   Every upcoming arrival is decided in one of three ways
   (`tools/sweep.py:run_sweep()`, fully deterministic - see
   `docs/how-it-works.md`): **invite** (first touch, inside the invite
   window), **follow-up** (chasing an unfinished check-in, inside the chase
   window and past the 48-hour cooldown), or **skip**, always with a reason
   printed to the terminal and recorded to `data/logs/*.jsonl`.

2. **The message is localized, not decided, by a model.** Each invite/
   follow-up is drafted by `prompts/invite.md` (`core.llm.complete()` with a
   JSON schema) in the guest's own language when it is one of
   `hotel.languages`, otherwise the property's default language with the
   item flagged `needs_human` - see `docs/safety.md`.

3. **If `llm.provider` is `interactive`,** the run stops with exit code 3 and
   parks a prompt in `data/pending/`. Read `*.prompt.md`, write your answer as
   JSON to the matching `*.answer.json` exactly matching the schema shown, and
   run the same command again.

4. **See what happened.**
   ```bash
   make review
   ```
   A guest whose language Self Check-In AI can reply in is `pending_review`.
   A guest in a language outside `hotel.languages` is `needs_human`, on
   purpose.

5. **Work the queue.** `workflows/80-review.md` covers approve / edit / reject
   / send in full.

6. **Keep it running.**
   ```bash
   make watch                       # loop on the configured interval
   ```
   Or schedule it - `make schedule` and `scheduler/` have cron, launchd and
   systemd examples. `config/agent.yaml`'s `schedule.sweep` documents the
   interval this repo was built around (hourly). The portal-sync leg is a
   separate job - `workflows/15-portal-sync.md`.

## Edge cases

- **No arrivals due.** `make run` prints `0 items processed, 0 drafted,
  0 sent` and exits 0. Nothing to do.
- **A message the model cannot draft cleanly.** `core.llm` raises
  `LLMSchemaError` rather than accept a bad answer; the item is queued as
  `needs_human` with the error recorded, instead of guessing.
- **A re-run the same day sees the same guest again.** Each drafted message
  is keyed `(res_ref, kind, today)` - a second pass the same day returns the
  existing item untouched (`tools/checkin.py:draft_and_queue_message`).
- **`invite_status` is this agent's own, not the PMS's.** The first time a
  reservation is seen it is synced into `checkin_guests`; after that, only
  the read-only PMS facts (name, room, dates, money) are refreshed on every
  pass. `invite_status`, `portal_step` and the rest are owned here, never
  overwritten from the PMS - see `docs/how-it-works.md`.
