# The `main` ruleset

`main-ruleset.json` is the branch protection for `main`, kept in the repository so the
configuration is reviewable in a pull request and restorable if it is ever changed by hand. GitHub
does not read it from here — it has to be applied.

## Applying it

Rulesets need the repository to be public, or on a paid plan. On a private free repository the
API returns `403 Upgrade to GitHub Pro or make this repository public`, which reads like a
permissions problem but is a billing one.

```bash
# Create. `{owner}/{repo}` is resolved by `gh` from the checkout you are standing in,
# so these work in a fork or after a rename.
gh api -X POST repos/{owner}/{repo}/rulesets \
  --input .github/main-ruleset.json

# Confirm
gh api repos/{owner}/{repo}/rulesets --jq '.[] | {id, name, enforcement}'

# Update later: find the id above, then
gh api -X PUT repos/{owner}/{repo}/rulesets/<id> \
  --input .github/main-ruleset.json
```

Verify it works by trying the thing it forbids:

```bash
git checkout main && git commit --allow-empty -m "ruleset check" && git push
# expected: rejected — "Changes must be made through a pull request"
git reset --hard HEAD~1
```

## What it enforces

| Rule | Effect |
| --- | --- |
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | No force-pushes to `main` |
| `pull_request` | Direct pushes rejected; changes arrive by pull request |
| `required_review_thread_resolution` | Every review conversation must be resolved before merge |
| `required_status_checks` → `ci-ok` | CI must pass |

`required_approving_review_count` is **0**, not 1. GitHub does not let anyone approve their own pull
request, so on a single-maintainer repository requiring an approval would make every pull request
permanently unmergeable. The gate here is CI plus resolved conversations, which is the part that
actually catches things.

`bypass_actors` is empty on purpose. With an admin bypass the rules become advisory, and the OP-15
acceptance criterion is specifically that a direct push to `main` is *rejected*. If a genuine
emergency ever needs it, set `enforcement` to `"evaluate"` briefly rather than adding a permanent
bypass.

**`ci-ok` is the only required check, deliberately.** The nine jobs beneath it skip routinely under
the path filtering added in OP-14 — a Python-only change skips all five `web-*` jobs — and naming
them individually would make merging depend on how GitHub treats skipped checks. Requiring the
aggregator also means a job can be renamed or split without silently dropping protection. See the
header comment in [`.github/workflows/pr.yml`](workflows/pr.yml).

**`scientific-ok`, `containers-ok` and `e2e-ok` are not required yet.** `scientific-validation.yml`
arrived with the shared threshold spec, `containers.yml` with the first Compose commit (OP-43), and
`e2e.yml` with the first full-stack journey (OP-47). Each runs its own aggregator on every pull
request, but the ruleset has not been updated to require them, so a red gate in any of them does
not currently block a merge. Adding them is a ruleset change, not a workflow change:

```bash
gh api -X PUT repos/{owner}/{repo}/rulesets/<id> --input .github/main-ruleset.json
```

after adding `scientific-ok`, `containers-ok` and `e2e-ok` to `required_status_checks` in the JSON.
Until then, treat a failure in any of them as blocking by convention. `scientific-ok` failing means
a metric moved, which should never merge unreviewed; `containers-ok` failing means `docker compose
up` does not work from a clean clone, which is the quickstart the README promises; `e2e-ok` failing
means a person cannot complete the one journey the application exists for.

## Two rules from the OP-15 ticket that are deliberately absent

**Linear history.** The ticket asked for it. Not applied: pull requests #10 through #13 all merged
as merge commits, which `required_linear_history` forbids, so enabling it would invalidate the
existing history's shape and force a switch to squash or rebase merges. It is a style preference
rather than a safety property, and the cost was not worth it. Decided 2026-07-26.

**Require branch up to date before merging** (`strict_required_status_checks_policy`). Also asked
for, also not applied. This project stacks pull requests — a ticket depending on unmerged work
branches from that branch rather than waiting. With a strict policy, merging the bottom of a stack
makes every branch above it stale, each needing a rebase and a full CI cycle, serially. `pr.yml`
already runs on every push to `main`, which catches a semantic conflict shortly after the fact
rather than preventing it. Accepted trade; revisit if a second contributor joins, when concurrent
merges become common.

## If a merge queue is ever enabled

Every required workflow must add `merge_group` to its `on:` triggers. Without it the queue waits
forever for checks that will never report, and the symptom looks like a hung queue rather than a
configuration error.
