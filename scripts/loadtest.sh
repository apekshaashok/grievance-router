#!/usr/bin/env bash
# Headless Locust run against the running stack (D6, A7). The model must be the stub (default .env.example).
#   scripts/loadtest.sh <users> [duration=3m] [label]
# Example sweep:  for u in 2 4 8 16; do scripts/loadtest.sh $u 3m w1; done
# Results: load/results/<label>-u<users>_stats.csv and the "SLO CHECK" line at the end of the output.
set -euo pipefail
USERS=${1:?users}
DURATION=${2:-3m}
LABEL=${3:-run}
mkdir -p load/results
docker compose --profile load run --rm -T \
  -v "$(pwd)/load/results:/mnt/results" \
  locust -f /mnt/locust/locustfile.py --headless -u "$USERS" -r "$USERS" -t "$DURATION" \
  --csv "/mnt/results/${LABEL}-u${USERS}" --only-summary 2>&1 | grep -E "SLO CHECK|E2E|Aggregated|POST|Error|error" || true
