#!/usr/bin/env bash
# Offline (free) test of fault handling + interruption/resume using the mock provider.
set -u
cd "$(dirname "$0")/.."
rm -rf runs/mock500 cache/mock500.db cache/mock500.db-wal cache/mock500.db-shm
run() { .venv/bin/python -m pipeline run --input data/checkpoint_500.csv --run-id mock500 --provider mock \
          --cache cache/mock500.db --batch-size 25 --mock-faults transient=7,malformed=5,drop=3,badquote=4 --quiet "$@"; }
run --stop-after-batches 6
echo "--- resuming"
run
