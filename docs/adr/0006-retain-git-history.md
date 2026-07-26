# ADR-0006 — Retain git history rather than rewrite it

**Status:** Accepted
**Date:** 2026-07-26
**Ticket:** OP-15 (decision applied throughout Epic A: OP-10, OP-11)

## Context

The inherited repository was 2.5 GB on disk with only ~150 tracked files, because ~2.2 GB of it was
already git-ignored and reproducible — virtualenvs, `node_modules`, and a 200 MB model download
(FINDINGS §7).

`.git` itself is 138 MB, and that part is not reproducible: it is coursework media committed to
history. The largest blobs are `Presentations/OpenPosturePhase2.pptx` (17 MB), `OPPoster.pdf`
(12 MB) and `PostureCapstone.pptx` (11 MB).

`git filter-repo` would take `.git` from 138 MB to roughly 15 MB. The question is whether that
is worth what it costs.

There is also a smaller history question. Commits `2002f8b` and `109d71b` hardcoded a path
containing a Firebase service-account filename (FINDINGS §5.4):

```
/Users/michaelnweke/PhpstormProjects/CS5588-Capstone-Project/API/db/openpose-db-firebase-adminsdk-pl8gq-05904164a8.json
```

The key **file** was never committed, so no private key is in history. What is permanently there is a
project id, a key-id fragment, and a previous machine's directory layout. It was removed from the
tree in `daff744`.

## Decision

**Do not rewrite git history.** Reduce the working tree instead: archive the coursework material to
`docs/archive/` (OP-10) and delete reproducible bulk and near-duplicate fixtures (OP-11), leaving
history untouched.

The reasoning is that **the history is evidence, and that is worth more than 123 MB.** This project's
premise is the genuine re-adoption of a real two-year-old team project — three contributors, a
university course, an audit, and a rewrite. A rewritten history would show a repository that appears
to have been assembled recently, and the most interesting thing about the project would become
unverifiable. A 138 MB clone is a few seconds on any modern connection.

The costs of rewriting are also higher than they first appear: every existing clone is invalidated,
every commit SHA changes — including the ones `docs/FINDINGS.md` cites as evidence, such as `2002f8b`
and `daff744` — and any force-push carries a genuine risk of damaging the remote. Rewriting history
to save disk on a repository nobody is short of disk for is a bad trade.

On the service-account path: **not worth a rewrite either.** No private key is exposed, so the
residual disclosure is a project id and a key-id fragment for a Firebase project that
[ADR-0003](0003-self-hosted-jwt.md) and [ADR-0004](0004-postgres-and-minio.md) remove from the system
entirely. The correct remediation is to decommission that Firebase project, which makes the
identifiers inert — not to rewrite history and hope no clone survives. If a real private key were
ever committed, this decision would be reversed immediately: that is a rotate-and-rewrite situation,
not a documentation one.

## Alternatives considered

**`git filter-repo` to strip large media from history.** The obvious option; rejected above. Worth
noting the media is not junk — it is the coursework record — so stripping it from history while
keeping it in `docs/archive/` means the files exist but their provenance does not.

**Start a fresh repository and import the old one as a subdirectory or a tag.** Rejected: it is
history rewriting with extra steps, and it breaks the continuity that makes the before-and-after
argument legible.

**Migrate the large media to Git LFS.** Rejected. Converting existing history to LFS is itself a
history rewrite with all the same costs, plus it adds a dependency on LFS storage and makes a plain
`git clone` incomplete. Actively worse than doing nothing.

**Keep everything, including the reproducible bulk.** Rejected. A 2.5 GB working tree is genuinely
obstructive — it blocked a Claude Code cloud session outright ("repo is too large to teleport") on
2026-07-25. Deleting what `uv sync` and `npm install` can regenerate costs nothing, and is a
different question from rewriting history.

## Consequences

- `.git` stays at ~138 MB. Clones are slower than they would otherwise be; nobody is meaningfully
  inconvenienced.
- Commit SHAs remain stable, so `docs/FINDINGS.md` can cite specific commits as evidence and those
  citations keep working. This is a precondition for the audit being checkable rather than merely
  assertable.
- The archived coursework in `docs/archive/` is deliberately immutable — FINDINGS references it by
  line number, so it is excluded from ruff, mypy, Prettier and pre-commit's fixers. It is evidence,
  not code, and reformatting it would misrepresent the material being cited.
- The Firebase project id and key-id fragment stay in history permanently. Mitigation is to
  decommission the project once Epic E replaces it; until then it is a live project reachable with
  identifiers that are public. **This is the one open action item created by this decision** and it
  should not be lost when Epic E closes.
- The tooling that needed a small working tree needs the remote connected instead of a teleported
  tree — a workflow change, not a repository change.
