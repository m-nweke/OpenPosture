# ADR-0004 — Postgres and MinIO, not Firebase Firestore and Storage

**Status:** Accepted
**Date:** 2026-07-26
**Ticket:** OP-15 (implemented in Epic E, from OP-30)

## Context

The original system stored data in Firebase: Firestore for records, Firebase Storage for images,
reached directly from the browser. The Flask API wrote uploads to a cwd-relative directory on local
disk (FINDINGS §3.5, §5.1) — so there were effectively two unrelated storage systems, one of which
lost its contents whenever the process was restarted from a different working directory.

v2 needs to store assessment history per user, keep the uploaded image alongside its result, and be
runnable end to end by someone who clones the repository. That last requirement is the one that
decides this.

## Decision

**Postgres** for relational data, **MinIO** for object storage, both as Docker Compose services.
SQLAlchemy 2.0 with Alembic migrations; MinIO accessed through the S3 API via `boto3`.

The reason is one property: **`docker compose up` gives a complete, working system with no
account anywhere.** No project to create, no credentials to provision, no free-tier quota, no
network. A reviewer clones the repo and runs it. That is worth more than any individual feature
difference, and it is the reason both halves of the decision are self-hosted rather than one.

MinIO speaks the S3 API, so the storage layer is written against S3 and can be pointed at real S3,
Cloudflare R2 or Backblaze B2 by changing an endpoint and credentials. Choosing self-hosted here
does not mean choosing a dead end.

Postgres over the alternatives is mostly unremarkable — it is the right default for relational data
with real constraints — but two things are specific to this project: the assessment records have
genuine relational shape (a user has many assessments, each with many findings and gaps), and
`jsonb` gives somewhere honest to keep the raw landmark payload without inventing 33 columns or a
second store.

## Alternatives considered

**Keep Firestore and Firebase Storage.** Rejected. It contradicts the self-hostable goal, requires a
live Google project to run or test anything, and — decisively — the data has relational shape that a
document store models badly. Scoping "every assessment belonging to this user, newest first, with
its findings" is a foreign key and an index in Postgres; in Firestore it is either denormalization or
a fan-out of reads. It also keeps the vendor that [ADR-0003](0003-self-hosted-jwt.md) removes.

**SQLite instead of Postgres.** Seriously considered, because it would remove a service from Compose
and make tests trivially fast. Rejected for two reasons: no `jsonb`, so the landmark payload becomes
a text column with no queryability; and it would mean developing against a different engine than
production, which is how subtle dialect bugs reach main. Testing against the real engine matters
more than the convenience.

**Local filesystem instead of MinIO.** What the original did. Rejected because it makes the API
stateful — the container cannot be replaced or scaled without losing images — and because writing
the storage layer against S3 from the start costs almost nothing while keeping every hosted option
open. It also removes an entire class of path-traversal and cwd-dependence bug that the original
had (FINDINGS §3.5).

## Consequences

- **Two more services to run.** Compose is now Postgres, MinIO, the API and the web app. Acceptable
  for a project whose selling point is that it runs locally, but it is real setup cost and the
  README must keep it to one command.
- **Postgres and MinIO cannot appear in `pr.yml`.** That workflow's guarantee is no database, no
  container, no network, no secret — it exists so the fast feedback loop stays fast. Anything needing
  these services belongs in `integration.yml`, which is why the split exists.
- Migrations are now a discipline. Alembic revisions must be committed with the model change that
  needs them, and a review has to check that a migration is reversible.
- Object keys must be opaque and server-generated. Deriving them from user-supplied filenames is how
  the original's upload route became a path-traversal risk.
- Uploaded images are personal data — photographs of people, held per user. Retention and deletion
  are therefore product requirements, not nice-to-haves, and "delete my account" has to delete
  objects as well as rows. Recorded here because a self-hosted store makes this the project's
  responsibility rather than a provider's.
- `jsonb` for raw landmarks is deliberately schemaless, which means it needs a version field.
  Landmark payloads written under `mediapipe==0.10.18` must remain interpretable if
  [ADR-0002](0002-mediapipe-pose.md) is ever reopened.
