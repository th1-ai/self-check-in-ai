# Sub-agents in this repo

None. The builder brief for Self Check-In AI lists no children, and none are
folded in here.

## The Locksmith boundary

The behavioural spec this repo was built from names a related capability,
"The Locksmith" (lock/key desk), and splits it in two:

| Capability | Where it lives |
|---|---|
| A guest's own door code, issued once their check-in completes | **here** - `tools/checkin.py:maybe_issue_door_code` |
| Contractor codes, the audit feed across every lock in the building, **revoking** a code, the "my code isn't working" resend loop | a housekeeping / access-control agent, not this one |

This repo only ever computes and drafts one guest's own stay-length door
code, at the point their check-in completes. It has no concept of a
contractor code, no audit feed across the building's other locks, and no
revoke action - a real access-control desk (managing every lock in the
property, not one guest's own code) is a different agent's job. If you build
one, it should own `systems.locks` end to end; this repo's
`tools/checkin.py:maybe_issue_door_code` is the one place here that touches
it, and it never calls anything but `issue_key`.

Revocation at checkout is explicitly **not** built here - see
`docs/how-it-works.md` design decision 3 for why, and what a real
access-control agent would need to do differently from the source this was
built from to avoid its bug.
