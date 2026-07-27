#!/usr/bin/env bash
# Merge the current `main` into every open stacked pull-request branch.
#
# Run this after each merge. Two things go stale otherwise:
#
#   1. GitHub's Files tab keeps the changeset it computed when the pull request was last pushed
#      to, so a branch whose base has moved shows its own changes *plus* everything merged since.
#      That is only a display artifact — the merge itself uses the true merge-base — but it makes
#      review unreliable, which is the whole point of one pull request per ticket.
#   2. A branch that has not seen a later `main` can develop a real conflict without saying so
#      until you try to merge it.
#
# Pushing the merge commit fixes both: GitHub recomputes on push.
#
# WHY MERGE RATHER THAN REBASE
# ----------------------------
# Rebasing rewrites the branch, which invalidates any review already left against those commits and
# races with whoever is merging. Merging is additive and safe to run repeatedly. The extra merge
# commits are noise in the branch, not in `main`, which only ever sees the pull request's own
# commits plus one merge.
#
# Usage:
#   scripts/sync-stack.sh            # merge origin/main into every open op-* branch
#   scripts/sync-stack.sh --dry-run  # report what would happen, change nothing

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

starting_branch=$(git rev-parse --abbrev-ref HEAD)
cleanup() { git checkout -q "$starting_branch" 2>/dev/null || true; }
trap cleanup EXIT

git fetch origin --prune --quiet

# A dirty tree turns a clean merge into a confusing one, so refuse rather than guess.
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
  echo "error: working tree has uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

# The open pull requests, in stack order. Read from GitHub rather than from local branches so a
# branch left behind after a merge is not resurrected.
branches=$(gh pr list --state open --limit 50 --json number,headRefName \
  --jq '.[] | select(.headRefName | startswith("op-")) | "\(.number) \(.headRefName)"' | sort -n)

if [[ -z "$branches" ]]; then
  echo "No open op-* pull requests."
  exit 0
fi

conflicted=()
while read -r number branch; do
  [[ -z "$branch" ]] && continue

  if git merge-base --is-ancestor origin/main "origin/$branch" 2>/dev/null; then
    printf '  #%-4s %-30s already up to date\n' "$number" "$branch"
    continue
  fi

  if $DRY_RUN; then
    git checkout -q -B _sync_check "origin/$branch"
    if git merge --no-commit --no-ff origin/main >/dev/null 2>&1; then
      printf '  #%-4s %-30s would merge clean\n' "$number" "$branch"
    else
      printf '  #%-4s %-30s WOULD CONFLICT: %s\n' "$number" "$branch" \
        "$(git diff --name-only --diff-filter=U | tr '\n' ' ')"
      conflicted+=("$number")
    fi
    git merge --abort 2>/dev/null || git reset -q --hard
    continue
  fi

  git checkout -q "$branch"
  git reset -q --hard "origin/$branch"
  if git merge --no-edit origin/main >/dev/null 2>&1; then
    git push -q origin "$branch"
    printf '  #%-4s %-30s merged and pushed\n' "$number" "$branch"
  else
    git merge --abort
    printf '  #%-4s %-30s CONFLICT — resolve by hand: %s\n' "$number" "$branch" \
      "$(git diff --name-only --diff-filter=U | tr '\n' ' ')"
    conflicted+=("$number")
  fi
done <<< "$branches"

git checkout -q "$starting_branch"
git branch -D _sync_check >/dev/null 2>&1 || true

if [[ ${#conflicted[@]} -gt 0 ]]; then
  echo
  echo "Conflicts on: ${conflicted[*]}"
  exit 1
fi
