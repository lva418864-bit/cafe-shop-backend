#!/bin/sh
# Start-up script for Docker (local compose) and Railway.
#  1. app.prestart: validate config, wait for PostgreSQL, prepare schema.
#  2. uvicorn on 0.0.0.0:$PORT (Railway sets/uses PORT; default 8002 locally).
# BIND_HOST can be set to "::" if the Railway environment uses IPv6-only private networking.
set -eu

python -m app.prestart

exec uvicorn app.main:app --host "${BIND_HOST:-0.0.0.0}" --port "${PORT:-8002}"
