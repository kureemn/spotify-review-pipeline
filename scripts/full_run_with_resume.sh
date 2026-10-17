#!/usr/bin/env bash
# Full-corpus run with a REAL interruption and resume, for the required recording.
#   Step 1  status before (all nonempty reviews pending, 13 empty texts quarantined)
#   Step 2  initial run: press Ctrl-C after ~1 minute; in-flight batches are saved, then it exits
#   Step 3  status after interruption (saved completed count)
#   Step 4  resume: same command; completed IDs are not re-sent; runs to completion
# caffeinate -i keeps the Mac from idle-sleeping while the pipeline runs.
set -u
cd "$(dirname "$0")/.."
trap 'echo' INT     # a handler (not "ignore") so Ctrl-C reaches the pipeline but not this script

RUN=(caffeinate -i .venv/bin/python -m pipeline run --input data/spotify_reviews_18months.csv --run-id full-v1
     --provider openai --confirm-paid --workers 10 --spend-limit 7.5)
banner() { printf '\n\033[1;32m==== %s ====\033[0m\n' "$1"; }

banner "STEP 1 - status before the run ($(date '+%H:%M:%S'))"
.venv/bin/python -m pipeline status --run-id full-v1

banner "STEP 2 - initial run (press Ctrl-C after about a minute to interrupt)"
"${RUN[@]}"

banner "STEP 3 - status after the interruption ($(date '+%H:%M:%S'))"
.venv/bin/python -m pipeline status --run-id full-v1
ls -la runs/full-v1/out/ | grep -E "enriched|records|run_log" || true

printf '\nPress Enter to RESUME the same run (completed IDs will not be sent again)... '
read -r _

banner "STEP 4 - resume ($(date '+%H:%M:%S'))"
"${RUN[@]}"

banner "DONE ($(date '+%H:%M:%S'))"
.venv/bin/python -m pipeline status --run-id full-v1
