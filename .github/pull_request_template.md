<!--
On this project the pull request body is the primary place the work is explained — it is read to
understand the code, not just to approve it. So prefer prose that teaches over a filled-in form.
Delete any heading that does not apply rather than writing "N/A" under it.
-->

## What this changes

<!-- What the reader will see differently after this merges. One or two paragraphs. -->

## Why this way

<!--
The reasoning, including alternatives rejected and why. This is the part worth writing: the diff
already says what changed, and only you can say why it changed that way.

If a decision here is architectural and expected to outlive the ticket, it belongs in an ADR under
docs/adr/ as well — link it.
-->

## How it was verified

<!--
The commands actually run and what they reported, not the ones that should pass. If something is
unverified — needs a real CI run, needs a deployed service, needs hardware — say so explicitly.
"Untested" is useful information; a claim of "tested" that turns out to mean "it compiled" is not.
-->

## Anything worth pushing back on

<!--
Scope calls, known gaps, deliberate omissions, anything left for a follow-up ticket. A reviewer
should not have to discover these by reading the diff.
-->

---

- Ticket: OP-
- [ ] Behaviour change is covered by a test that fails without it
- [ ] Coverage floors still met (`posture-core` 95%, `apps/web` 70%)
- [ ] No new dependency in `posture-core` beyond numpy (enforced by `test_dependency_isolation.py`)
- [ ] Docs updated if this changes how someone runs or builds the project
