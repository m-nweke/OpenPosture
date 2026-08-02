#!/bin/sh
# Container entrypoint: migrate then start.
#
# `set -e` is what makes migration failure a startup failure. If `alembic upgrade head` exits
# non-zero — connection refused, DDL error, a migration that raises — this script exits and the
# container never reaches `exec "$@"`. The orchestrator sees the container exit before its
# healthcheck passes, marks it unhealthy, and does not route traffic to it.
#
# Without this guard, the application would start against a schema that does not match its models
# and surface the mismatch as a runtime error — during a real request, not at startup.
set -e

alembic -c /app/apps/api/alembic.ini upgrade head

exec "$@"
