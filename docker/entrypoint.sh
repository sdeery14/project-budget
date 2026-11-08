#!/bin/sh
# Container entrypoint (ADR-010): apply migrations, then start the single-worker server.
set -eu

projectbudget db upgrade
exec projectbudget run "$@"
