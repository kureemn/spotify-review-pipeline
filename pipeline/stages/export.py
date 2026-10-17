"""Exports: enriched table, quarantine log, standardized grading/ folder, run summary.

Unfinished (pending) rows are exported as quarantined with reason `not_processed_pending` so
every source ID is accounted for, but they never count as completed classifications.
"""

import gzip
import json
import shutil
import sqlite3
from pathlib import Path

from ..config import RUNS

GRADING_VERSION = "a5-audit-v1"


def record_rows(con):
    q = """SELECT v.review_id, v.source_sha256, v.status, v.reason, v.attempts, r.topic, r.subtopic, r.intent, r.sentiment,
                  r.severity, r.entities, r.evidence_quote, r.needs_review, r.note, r.label_config, r.cache_source_id,
                  r.origin_request_id, r.origin_run_id, r.quote_method
           FROM reviews v LEFT JOIN results r USING(review_id) ORDER BY v.src_order"""
    for (rid, sha, status, reason, attempts, topic, sub, intent, sent, sev, ents, quote, nr, note, lc, csid,
         oreq, orun, qm) in con.execute(q):
        if status == "completed":
            rec = {"review_id": rid, "source_sha256": sha, "status": "completed", "topic": topic, "intent": intent,
                   "sentiment": sent, "severity": sev, "entities": json.loads(ents), "evidence_quote": quote,
                   "needs_review": bool(nr), "label_config": lc}
            if csid:
                rec["cache_source_id"] = csid
            yield rec, {"subtopic": sub, "review_note": note, "quote_method": qm, "origin_request_id": oreq,
                        "origin_run_id": orun, "attempts": attempts}
        else:
            yield ({"review_id": rid, "source_sha256": sha, "status": "quarantined",
                    "reason": reason if status == "quarantined" else "not_processed_pending"},
                   {"attempts": attempts, "pipeline_status": status})


def export_tables(store, out: Path):
    """enriched.jsonl (completed + subtopic/provenance), quarantine.jsonl, records.jsonl."""
    n = {"completed": 0, "quarantined": 0, "pending_exported_as_quarantined": 0, "cache_reuse": 0}
    with open(out / "enriched.jsonl", "w") as fe, open(out / "quarantine.jsonl", "w") as fq, \
            open(out / "records.jsonl", "w") as fr:
        for rec, extra in record_rows(store.con):
            fr.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec["status"] == "completed":
                n["completed"] += 1
                n["cache_reuse"] += "cache_source_id" in rec
                fe.write(json.dumps(rec | extra, ensure_ascii=False) + "\n")
            else:
                n["quarantined"] += 1
                n["pending_exported_as_quarantined"] += extra.get("pipeline_status") == "pending"
                fq.write(json.dumps(rec | extra, ensure_ascii=False) + "\n")
    return n


def call_rows(store):
    """Grading-shape call log for this run plus any origin runs whose results were reused by review ID."""
    cols = ("request_id", "run_id", "session", "role", "phase", "provider", "model", "effort", "prompt_version",
            "label_config", "outcome", "error", "review_ids", "attempt", "input_tokens", "cached_tokens",
            "cache_write_tokens", "output_tokens", "reasoning_tokens", "usage_available", "cost_usd",
            "started_at", "ended_at", "latency_s", "artifacts", "service_tier")
    sources = [store.con]
    origin_runs = [r[0] for r in store.con.execute(
        "SELECT DISTINCT origin_run_id FROM results WHERE origin_run_id IS NOT NULL")]
    this_run = store.get("run_id")
    for orun in origin_runs:
        if orun != this_run and (RUNS / orun / "state.db").exists():
            sources.append(sqlite3.connect(RUNS / orun / "state.db"))
    seen = set()
    for con in sources:
        for row in con.execute(f"SELECT {', '.join(cols)} FROM calls ORDER BY started_at"):
            c = dict(zip(cols, row))
            if c["request_id"] in seen:
                continue
            seen.add(c["request_id"])
            c["review_ids"] = json.loads(c["review_ids"]) if c["review_ids"] else []
            c["artifacts"] = json.loads(c["artifacts"]) if c["artifacts"] else None
            if c["role"] != "enrich":
                c["phase"] = c["phase"] or "downstream"
            yield c


def export_grading(store, out: Path, grading: Path, compress=True):
    grading.mkdir(parents=True, exist_ok=True)
    inp = store.get("input")
    (grading / "run.json").write_text(json.dumps({
        "version": GRADING_VERSION, "analysis_count": inp["records"], "analysis_sha256": inp["file_sha256"],
        "classification_input_fields": ["review_text"], "allow_multi_issue": False,
        "run_id": store.get("run_id"), "label_config": store.get("label_config"),
        "declared_scope": "Every source review ID; nonempty texts classified by the enrich role, empty texts quarantined."},
        indent=2) + "\n")
    shutil.copy(out / "ingestion.json", grading / "ingestion.json")
    for name in ("membership.csv", "ranking.csv", "claims.csv"):
        shutil.copy(out / name, grading / name)
    for name in ("checkpoint_before.json", "checkpoint_after.json"):
        if (out / name).exists():
            shutil.copy(out / name, grading / name)

    def write_jsonl(name, rows):
        for stale in (grading / name, grading / (name + ".gz")):
            stale.unlink(missing_ok=True)
        opener = (lambda p: gzip.open(p, "wt", encoding="utf-8")) if compress else (lambda p: open(p, "w"))
        with opener(grading / (name + (".gz" if compress else ""))) as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    write_jsonl("records.jsonl", (rec for rec, _ in record_rows(store.con)))
    write_jsonl("calls.jsonl", call_rows(store))
