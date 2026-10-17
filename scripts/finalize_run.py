"""Post-run evidence (code only, no model calls):

  runs/<id>/data_manifest.json              source identity + code/prompt/model settings + sha256 of every output
  runs/<id>/out/monthly_complaint_share.csv  each area's share of all complaint/cancellation records per month

    .venv/bin/python scripts/finalize_run.py --run runs/full-v1
"""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.source import csv_rows  # noqa: E402  (course strict CSV reader)

AREAS = {"access": ["access"], "usability": ["usability"], "playback": ["playback"], "downloads": ["downloads"],
         "catalog": ["catalog"], "billing": ["billing"], "support": ["support"], "other": ["other"],
         "billing_support": ["billing", "support"]}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--source", type=Path, default=ROOT / "data" / "spotify_reviews_18months.csv")
    a = ap.parse_args()
    a.run = a.run.resolve()
    out = a.run / "out"

    # ---- monthly complaint share (denominator = all complaint/cancellation records that month)
    month_of = {r["review_id"]: r["review_timestamp"][:7] for r in csv_rows(a.source)}
    counts = Counter()
    with open(out / "enriched.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            m = month_of[r["review_id"]]
            counts[(m, "_completed_all")] += 1
            if r["intent"] in ("complaint", "cancellation"):
                counts[(m, "_complaints_all")] += 1
                for area, members in AREAS.items():
                    if r["topic"] in members:
                        counts[(m, area)] += 1
    months = sorted({m for m, _ in counts})
    with open(out / "monthly_complaint_share.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["month", "partial_month", "completed_records", "all_complaints", "area", "area_complaints", "share_pct"])
        for m in months:
            total = counts[(m, "_complaints_all")]
            for area in AREAS:
                n = counts[(m, area)]
                w.writerow([m, m in (months[0], months[-1]), counts[(m, "_completed_all")], total, area, n,
                            f"{100 * n / total:.1f}" if total else ""])

    # ---- data manifest
    run_manifest = json.loads((a.run / "run_manifest.json").read_text())
    ingestion = json.loads((out / "ingestion_report.json").read_text())
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    outputs = {}
    for p in sorted(list(out.rglob("*")) + list((ROOT / "grading").glob("*")) + list((ROOT / "release").glob("*"))):
        if p.is_file() and p.name != ".DS_Store":
            outputs[str(p.relative_to(ROOT))] = {"sha256": sha(p), "bytes": p.stat().st_size}
    manifest = {
        "run_id": a.run.name,
        "source": {"file": ingestion["input_path"], "sha256": ingestion["file_sha256"], "bytes": ingestion["bytes"],
                   "records": ingestion["records"], "parsed_rows_sha256": ingestion["parsed_rows_sha256"],
                   "dataset": "BwandoWando, 3.4 Million Spotify Google Store Reviews v2 (Kaggle, CC0); course extract 2022-05-17..2023-11-17"},
        "code": {"git_commit_at_finalize": commit, "pipeline_at_run_start": run_manifest["code"]},
        "label_config": run_manifest["label_config"],
        "models_and_settings": {role: run_manifest["config"][role] for role in ("enrich", "verify", "group", "memo")},
        "prompts_sha256_12": run_manifest["prompts"],
        "declared": {k: run_manifest["config"][k] for k in ("verify_rate", "verify_seed", "short_quote_chars", "max_invalid_retries",
                                                            "max_transient_retries", "project_budget_usd", "spend_limit_usd")},
        "outputs": outputs,
        "note": "enriched.jsonl / records.jsonl are large: gzipped copies are in grading/ and the GitHub release (release/).",
    }
    (a.run / "data_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote data_manifest.json ({len(outputs)} outputs hashed) and monthly_complaint_share.csv ({len(months)} months)")


if __name__ == "__main__":
    main()
