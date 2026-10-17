"""Automated, recorded interruption/resume of the FULL run (runs/full-v1).

1. status before   2. initial run, then a real SIGINT (Ctrl-C) after INTERRUPT_AFTER seconds
3. status after the interruption (saved progress)   4. resume with the same settings; it is
detached so it keeps running after the recorded segment, with output in resume_console.log.

Run under `script -r` for a timed terminal recording:
    script -q -r docs/evidence/resume_demo.rec .venv/bin/python scripts/resume_demo.py
"""

import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
RUN = [PY, "-m", "pipeline", "run", "--input", "data/spotify_reviews_18months.csv", "--run-id", "full-v1",
       "--provider", "openai", "--confirm-paid", "--workers", "10", "--spend-limit", "7.5"]
STATUS = [PY, "-m", "pipeline", "status", "--run-id", "full-v1"]
COUNTDOWN, INTERRUPT_AFTER, RESUME_SHOWN = 20, 60, 45
LOG = ROOT / "runs" / "full-v1" / "out" / "resume_console.log"


def banner(text):
    print(f"\n\033[1;32m==== {text} ({datetime.now():%H:%M:%S}) ====\033[0m", flush=True)


def main():
    for s in range(COUNTDOWN, 0, -5):
        print(f"[demo] starting in {s}s ...", flush=True)
        time.sleep(5)
    banner("STEP 1 - status before the full run")
    subprocess.run(STATUS, cwd=ROOT)

    banner(f"STEP 2 - initial run; a real Ctrl-C (SIGINT) will be sent after {INTERRUPT_AFTER}s")
    p = subprocess.Popen(RUN, cwd=ROOT)
    try:
        p.wait(timeout=INTERRUPT_AFTER)
    except subprocess.TimeoutExpired:
        print(f"\n\033[1;33m[demo] sending Ctrl-C (SIGINT) to pid {p.pid} at {datetime.now():%H:%M:%S}\033[0m", flush=True)
        p.send_signal(signal.SIGINT)
        p.wait()
    print(f"[demo] initial run exited with code {p.returncode}", flush=True)

    banner("STEP 3 - status after the interruption: saved work is on disk")
    subprocess.run(STATUS, cwd=ROOT)
    time.sleep(3)

    banner("STEP 4 - resume with identical settings (completed IDs are not sent again)")
    log = open(LOG, "a")
    r = subprocess.Popen(RUN, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    subprocess.Popen(["caffeinate", "-i", "-w", str(r.pid)], start_new_session=True)   # no idle sleep until done
    end = time.time() + RESUME_SHOWN
    with open(LOG) as f:
        f.seek(0, 2)
        while time.time() < end:
            line = f.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.2)
    banner(f"recorded segment ends; resume continues in the background (pid {r.pid}), log {LOG.relative_to(ROOT)}")
    subprocess.run(STATUS, cwd=ROOT)


if __name__ == "__main__":
    main()
