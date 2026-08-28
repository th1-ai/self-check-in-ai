---
name: self-check-in-ai
description: Run Self Check-In AI ("The Gatekeeper") — Runs the entire check-in before the guest reaches the desk.. Use when the user asks to run the agent, check what is waiting for review, approve or reject a draft, or asks how the agent is doing. Trigger phrases: "run The Gatekeeper", "/self-check-in-ai", "check the queue", "what is waiting for me", "approve that draft".
---

# Self Check-In AI

Runs both halves of Self Check-In AI - the pre-arrival sweep and the portal
sync leg - and works the review queue. Everything happens from the repo
root; every command below exists and works.

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-checkin-sweep.md`
plus `workflows/15-portal-sync.md` for the two loops. If the user has never
run this agent, start at `workflows/00-setup.md` instead and walk them
through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines are
worth mentioning but do not stop the run.

**2. Run the sweep, then the portal sync.**

```bash
make run                                  # invite / follow-up / skip, deterministic
make run ARGS="--dry-run"                 # compute everything, write nothing
python3 tools/portal_sync.py --once       # apply what guests did in the portal
```

If `llm.provider` is `interactive`, a run will stop with exit code 3 and park
prompts in `data/pending/`. That is expected. Read each `*.prompt.md`, write your
answer as JSON to the matching `*.answer.json` following the schema exactly, then
run the same command again.

**3. Show what is waiting.**

```bash
make review
python3 tools/review.py list --status needs_human --kind checkin_escalation
python3 tools/review.py show <id>
```

Summarise it for the user in plain language: which guest, which of the four
kinds (invite/follow-up email, door-code notice, upsell folio post, or an
escalation with no draft), and why it needs a look. Do not paste raw JSON at
them.

**4. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file <path>
python3 tools/review.py reject <id> --reason "<why>"
```

Read the draft back to them before approving. An escalation has no draft -
resolve it in person, then close it with `reject --reason "resolved: ..."`.
An edit's before/after is stored, so it is not lost even without a coach
layer in this repo.

**5. Report.**

```bash
make report
```

## Rules

- **Never send or post in shadow mode**, and never work around a blocked write.
  The error message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through.
- **Confirm before anything irreversible** — a guest email, a PMS folio note, a
  door-code message — even when it is approved.
- **Never print or paste a credential.**
- If a run fails, read the whole error, fix the cause, re-run, and note what you
  learned in `workflows/99-troubleshooting.md`.
