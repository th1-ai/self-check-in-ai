# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`agent windows` / `agent rules`: no block in config/agent.yaml.** Copy
  `config/agent.example.yaml` to `config/agent.yaml` - `make setup` should
  have done this; re-run it if not.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic` in `config/hotel.yaml`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail loud
  when misconfigured (a `warn` is reserved for stubs). Read the `detail`
  column - it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock`, reads
  `fixtures/hotel/reservations.json` for the sweep and `fixtures/inbound/*.json`
  for the portal sync leg - if you deleted or renamed those files, restore
  them from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow errors
  on purpose, so a fixture problem shows up immediately.

## `make run` or `python3 tools/portal_sync.py` exits with code 3

Not an error. `llm.provider: interactive` parked a prompt. Read
`data/pending/*.prompt.md`, write your answer to the matching
`*.answer.json` (JSON only, matching the schema shown, no prose, no code
fence), and run the same command again.

## An escalation will not close

`checkin_escalation` items have no draft - `approve` and `send` do not apply
to them. Close one with:

```bash
python3 tools/review.py reject <id> --reason "resolved: <what you did>"
```

## A portal event says "unknown reservation - run the sweep first"

`tools/portal_sync.py` only applies events for a reservation this agent has
already synced into `checkin_guests`. Run `python3 tools/run.py --once` (or
wait for the next scheduled sweep) for that arrival, then re-run the sync.

## The door code will not open the actual door

It never will on its own - `systems.locks` is a stub in this repo (see
`docs/integrations.md#locks` and `docs/how-it-works.md` "Door codes: honest
about hardware"). The computed code and the queued guest message are real;
programming a physical lock needs your own vendor's adapter, or a person
doing it by hand.

## An item is stuck at `sending`

A process died between claiming an item and finishing the send.
`tools/run.py` calls `core.store.Store.reap_stuck_sending()` on every pass,
which moves anything stuck for more than 30 minutes to `failed` so you see it
in the queue instead of it vanishing. Use
`python3 tools/review.py retry <id>` once the cause is fixed.

## The invite/follow-up draft gets the tone or the facts wrong

Fix it in the review queue first (`edit`, not `reject`, so the correction is
recorded), then look at whether `prompts/invite.md` needs a clearer
instruction or `knowledge/checkin-policy.md` / `knowledge/property.md` is
missing the fact. Prompts are plain markdown - edit them directly and re-run.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id. `python3 tools/review.py show <id>` has the full event trail for one
item. If neither explains it, that is a real bug - describe exactly what you
ran and what you expected, and ask.
