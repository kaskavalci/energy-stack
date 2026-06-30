#!/bin/sh
set -e

# Seed price CSVs after EMHASS is healthy (same HA path as MPC)
(
  sleep 90
  python3 /app/fetch_prices_emhass.py --write-only
) &

exec supercronic -passthrough-logs /app/crontab
