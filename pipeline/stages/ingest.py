"""Stage 1 - Prepare (code only, no model).

Reads every row with a strict multiline-safe CSV parser, preserves source values and IDs,
hashes each row exactly as the grader does, profiles quality, finds exact duplicate texts,
quarantines empty texts and queues everything else as pending.
"""

import json
import time
from collections import Counter

from ..source import csv_rows, file_sha, profile, row_sha, text_sha

EXPECTED_FULL = {"records": 660622, "duplicate_review_ids": 0, "empty_review_text": 13,
                 "missing_app_version": 159701, "distinct_nonempty_texts": 484189,
                 "file_sha256": "1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6"}


def run(store, csv_path, out_dir, log):
    t0 = time.time()
    if store.get("ingested"):
        log("ingest", "skip", reason="already ingested", counts=store.counts())
        return json.loads((out_dir / "ingestion_report.json").read_text())

    sha = file_sha(csv_path)
    rows, seen_ids, dup_ids = [], set(), []
    text_counts, ratings, months = Counter(), Counter(), Counter()
    empty, missing_version, bad_rating = [], 0, 0
    for order, row in enumerate(csv_rows(csv_path)):
        rid = row["review_id"]
        if rid in seen_ids:
            dup_ids.append(rid)           # kept visible; second copy cannot be stored under the same ID
            continue
        seen_ids.add(rid)
        text = row["review_text"]
        status, reason = "pending", None
        if not text.strip():
            status, reason = "quarantined", "empty_review_text"
            empty.append(rid)
        else:
            text_counts[text] += 1
        missing_version += not row["app_version"].strip()
        ratings[row["review_rating"]] += 1
        bad_rating += row["review_rating"] not in {"1", "2", "3", "4", "5"}
        months[row["review_timestamp"][:7]] += 1
        rows.append((rid, order, text, row["review_rating"], row["review_likes"], row["app_version"],
                     row["review_timestamp"], row_sha(row), text_sha(text), status, reason))

    with store.tx() as c:
        c.executemany("INSERT INTO reviews(review_id, src_order, review_text, review_rating, review_likes, app_version, "
                      "review_timestamp, source_sha256, text_sha, status, reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)

    dup_groups = [n for n in text_counts.values() if n > 1]
    grading_profile = profile(csv_path)   # exact helper output for grading/ingestion.json
    report = {
        "input_path": str(csv_path.name), "file_sha256": sha, "bytes": csv_path.stat().st_size,
        "records": len(rows) + len(dup_ids), "unique_review_ids": len(rows), "duplicate_review_ids": len(dup_ids),
        "empty_review_text": len(empty), "empty_review_ids": empty,
        "nonempty_to_classify": len(rows) - len(empty),
        "distinct_nonempty_texts": len(text_counts),
        "exact_duplicate_text_groups": len(dup_groups),
        "rows_in_duplicate_text_groups": sum(dup_groups),
        "reusable_rows_if_exact_cache": sum(dup_groups) - len(dup_groups),
        "missing_app_version": missing_version, "invalid_rating_values": bad_rating,
        "reviews_by_rating": dict(sorted(ratings.items())), "reviews_by_month": dict(sorted(months.items())),
        "first_review": grading_profile["first_review"], "last_review": grading_profile["last_review"],
        "parsed_rows_sha256": grading_profile["parsed_rows_sha256"],
        "pending_queue": len(rows) - len(empty),
        "quarantine_policy": "Only empty/whitespace-only texts are quarantined at ingestion (reason empty_review_text). "
                             "Missing app_version does not block classification.",
        "seconds": round(time.time() - t0, 2),
    }
    if sha == EXPECTED_FULL["file_sha256"]:
        report["expected_full_file_checks"] = {k: {"expected": v, "observed": report.get(k, sha), "pass": report.get(k, sha) == v}
                                               for k, v in EXPECTED_FULL.items()}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ingestion_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (out_dir / "ingestion.json").write_text(json.dumps(grading_profile, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    (out_dir / "quarantine_ingest.jsonl").write_text(
        "".join(json.dumps({"review_id": r, "status": "quarantined", "reason": "empty_review_text", "attempts": 0}) + "\n"
                for r in empty))
    store.put("ingested", True)
    store.put("input", {"path": str(csv_path), "file_sha256": sha, "records": report["records"]})
    log("ingest", "done", records=report["records"], pending=report["pending_queue"], quarantined=len(empty),
        distinct_texts=len(text_counts), seconds=report["seconds"])
    return report
