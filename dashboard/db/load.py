"""Load saved pipeline outputs into Postgres (Neon). No model calls; re-runnable.

    .venv/bin/python dashboard/db/load.py --run runs/full-v1
Connection string: DATABASE_URL from the environment or dashboard/.env.local (`vercel env pull`).
"""

import argparse
import csv
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
AREAS = {"access": ["access"], "usability": ["usability"], "playback": ["playback"], "downloads": ["downloads"],
         "catalog": ["catalog"], "billing": ["billing"], "support": ["support"], "other": ["other"],
         "billing_support": ["billing", "support"]}


def db_url():
    url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / "dashboard" / ".env.local").get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not found. Run `vercel env pull .env.local` inside dashboard/ after connecting Neon.")
    return url


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    a = ap.parse_args()
    run, out = a.run, a.run / "out"
    t0 = time.time()

    src = sqlite3.connect(f"file:{run / 'state.db'}?mode=ro", uri=True)
    meta = {rid: (order, rating, ts, ver) for rid, order, rating, ts, ver in src.execute(
        "SELECT review_id, src_order, review_rating, review_timestamp, app_version FROM reviews")}
    membership = {r["review_id"]: r["issue_id"] for r in read_csv(out / "membership.csv")}
    records = list(jsonl(out / "enriched.jsonl")) + list(jsonl(out / "quarantine.jsonl"))
    assert len(records) == len(meta), f"{len(records)} exported records vs {len(meta)} source rows"

    ranking = {r["issue_id"]: r for r in read_csv(out / "aggregates.csv")}
    issues_doc = json.loads((out / "issues.json").read_text())
    topics = read_csv(out / "topic_aggregates.csv")
    claims = read_csv(out / "claims.csv")
    memo = json.loads((out / "memo.json").read_text())
    summary = json.loads((out / "run_summary.json").read_text())
    ingestion = json.loads((out / "ingestion_report.json").read_text())
    manifest = json.loads((run / "run_manifest.json").read_text())
    verify = json.loads((out / "verify" / "verify_summary.json").read_text())

    # Monthly complaint share per area (code-computed; denominators kept).
    monthly = Counter()
    for r in records:
        if r["status"] != "completed":
            continue
        month = meta[r["review_id"]][2][:7]
        complaint = r["intent"] in ("complaint", "cancellation")
        for area, members in AREAS.items():
            if r["topic"] in members:
                monthly[(month, area, "completed")] += 1
                monthly[(month, area, "complaints")] += complaint
        monthly[(month, "all", "completed")] += 1
        monthly[(month, "all", "complaints")] += complaint

    metrics = {
        "run": {"run_id": run.name, "label_config": summary["label_config"], "source_file": ingestion["input_path"],
                "source_sha256": ingestion["file_sha256"], "review_window": [ingestion["first_review"], ingestion["last_review"]]},
        "coverage": {"source_rows": ingestion["records"], "nonempty": ingestion["nonempty_to_classify"],
                     "completed": summary["status_counts"].get("completed", 0),
                     "quarantined": summary["status_counts"].get("quarantined", 0),
                     "pending": summary["status_counts"].get("pending", 0),
                     "quarantine_reasons": summary["quarantine_reasons"],
                     "distinct_texts": ingestion["distinct_nonempty_texts"],
                     "exact_text_reuse_records": summary["cache_reuse_records"],
                     "complaint_or_cancellation": len(membership), "issues": len(ranking)},
        "cost": {"api_usd_estimated_from_usage": summary["spent_usd_estimated_from_usage"],
                 "calls": {role: sum(v["calls"] for v in d.values()) for role, d in summary["calls"].items()},
                 "failed_calls": {role: d.get("failed", {}).get("calls", 0) for role, d in summary["calls"].items()},
                 "tiers": summary.get("service_tiers"), "spend_limit_usd": summary["spend_limit_usd"],
                 "project_budget_usd": summary.get("project_budget_usd"),
                 "wall_clock_s": sum(w["seconds"] for w in summary["wall_clock"]),
                 "note": summary["cost_note"]},
        "verification": {k: verify[k] for k in ("declared_rate", "verified", "topic_agreement", "intent_agreement",
                                                "severity_exact", "severity_within_1", "severity_mae", "disagreements")},
        "resume": [{k: s[k] for k in ("session", "phase", "completed_before", "completed_after", "stop_reason")}
                   for s in summary["sessions"] if s["stage"] == "enrich"],
    }
    for name, path in {"golden": ROOT / "evals" / "golden" / "golden_summary.json",
                       "planted": ROOT / "evals" / "planted" / "summary.json",
                       "planted_subtle": ROOT / "evals" / "planted_subtle" / "summary.json",
                       "injection": ROOT / "evals" / "injection" / "summary.json",
                       "cost_calculator": ROOT / "cost" / "report.json"}.items():
        if path.exists():
            d = json.loads(path.read_text())
            if name == "golden":
                d = {k: d[k] for k in ("cases", "ambiguous_cases", "missing_or_quarantined_predictions", "agreement_strict",
                                       "agreement_accepted_set", "severity_mae_vs_primary", "sentiment_mae")}
            if name == "cost_calculator":
                d = {"pilot_cold": {k: d["measured"]["cold"][k] for k in ("api_usd", "wall_clock_s", "status_counts")},
                     "pilot_warm": {k: d["measured"]["warm"][k] for k in ("api_usd", "wall_clock_s", "new_enrichment_calls",
                                                                          "new_downstream_calls")},
                     "projections": [{k: p[k] for k in ("scenario", "label", "total_api_usd", "elapsed_hours", "exceeds_budget")}
                                     for p in d["projections"]]}
            metrics[name] = d

    with psycopg.connect(db_url(), autocommit=False) as con, con.cursor() as cur:
        cur.execute((ROOT / "dashboard" / "db" / "schema.sql").read_text())
        cur.execute("INSERT INTO runs VALUES (%s,%s,%s,now(),%s)",
                    (run.name, summary["label_config"], ingestion["file_sha256"], json.dumps(manifest)))
        with cur.copy("COPY reviews (review_id, src_order, review_rating, review_timestamp, app_version, status, reason, "
                      "topic, subtopic, intent, sentiment, severity, needs_review, review_note, evidence_quote, entities, "
                      "cache_source_id, label_config, issue_id) FROM STDIN") as cp:
            for r in records:
                order, rating, ts, ver = meta[r["review_id"]]
                done = r["status"] == "completed"
                cp.write_row((r["review_id"], order, int(rating) if rating.isdigit() else None, ts or None, ver or None,
                              r["status"], r.get("reason"), r.get("topic"), r.get("subtopic"), r.get("intent"),
                              r.get("sentiment"), r.get("severity"), r.get("needs_review") if done else None,
                              r.get("review_note"), r.get("evidence_quote"), r.get("entities") if done else None,
                              r.get("cache_source_id"), r.get("label_config"), membership.get(r["review_id"])))
        for i in issues_doc["issues"]:
            g = ranking[i["issue_id"]]
            cur.execute("INSERT INTO issues VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (
                i["issue_id"], int(g["rank"]), i["topic"], i["subtopic"], i["name"], i["summary"], i["coherence"],
                int(g["complaint_count"]), int(g["severity_sum"]), g["mean_severity"], int(g["priority_score"]),
                int(g["cancellation_count"]), int(g["needs_review_count"]), [int(g[f"sev{k}"]) for k in range(1, 6)],
                i["example_ids"]))
        cur.executemany("INSERT INTO topic_aggregates VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [tuple(t[k] for k in ("area", "completed_records", "complaint_records", "cancellation_records",
                                              "severity_sum", "mean_severity", "sev4plus_complaints",
                                              "share_of_all_complaints", "share_of_completed")) for t in topics])
        months = sorted({(m, ar) for m, ar, _ in monthly})
        cur.executemany("INSERT INTO monthly VALUES (%s,%s,%s,%s)",
                        [(m, ar, monthly[(m, ar, "completed")], monthly[(m, ar, "complaints")]) for m, ar in months])
        cur.executemany("INSERT INTO claims VALUES (%s,%s,%s,%s)",
                        [(c["claim_id"], c["issue_id"], c["metric"], c["value"]) for c in claims])
        cur.execute("INSERT INTO memo VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (run.name, memo.get("priority_area"), memo.get("headline"), memo.get("memo_markdown"), memo.get("status"),
                     json.dumps(memo.get("validation_problems", [])), memo.get("cited_claims", []),
                     manifest["config"]["memo"]["model"]))
        cur.executemany("INSERT INTO metrics VALUES (%s,%s)", [(k, json.dumps(v)) for k, v in metrics.items()])
        con.commit()
        cur.execute("SELECT (SELECT COUNT(*) FROM reviews), (SELECT COUNT(*) FROM issues), "
                    "(SELECT pg_size_pretty(pg_database_size(current_database())))")
        n_reviews, n_issues, size = cur.fetchone()
    print(f"loaded {n_reviews:,} reviews, {n_issues} issues, {len(claims)} claims from {run} in {time.time() - t0:.1f}s; db size {size}")


if __name__ == "__main__":
    main()
