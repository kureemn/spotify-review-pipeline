"""Stage 5 - Rank (code only, no model).

priority(issue) = complaint_count x mean_severity = severity_sum, over completed complaint and
cancellation records, one issue per review. Sorted by descending score, then ascending issue_id.
Means are Decimal half-up to six places, exactly as the course checker computes them.

`python -m pipeline rank --from runs/<id>/out` regenerates everything here from saved files only.
"""

import csv
import gzip
import json
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from ..labels import TOPICS

RANKED_INTENTS = {"complaint", "cancellation"}


def mean6(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def pct(a, b, places="0.1"):
    return str((Decimal(100 * a) / Decimal(b)).quantize(Decimal(places), rounding=ROUND_HALF_UP)) + "%" if b else "n/a"


def read_jsonl(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_membership(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return [(r["issue_id"], r["review_id"]) for r in csv.DictReader(f)]


def build_membership(records):
    """Deterministic grouping rule: issue_id = topic.subtopic of each completed complaint/cancellation."""
    return sorted(((f"{r['topic']}.{r['subtopic']}", r["review_id"]) for r in records
                   if r.get("status") == "completed" and r["intent"] in RANKED_INTENTS),
                  key=lambda p: (p[0], p[1]))


def ranking(records, membership):
    by_id = {r["review_id"]: r for r in records if r.get("status") == "completed"}
    members = defaultdict(list)
    for iid, rid in membership:
        members[iid].append(by_id[rid]["severity"])
    rows = [{"issue_id": iid, "complaint_count": len(v), "severity_sum": sum(v),
             "mean_severity": mean6(sum(v), len(v)), "priority_score": sum(v)} for iid, v in members.items()]
    rows.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for k, row in enumerate(rows, 1):
        row["rank"] = k
    return rows


def aggregates(records, membership, ranked):
    """Issue-level detail plus topic-level rollups (descriptive; the baseline ranking is above)."""
    by_id = {r["review_id"]: r for r in records}
    detail = []
    members = defaultdict(list)
    for iid, rid in membership:
        members[iid].append(by_id[rid])
    for row in ranked:
        m = members[row["issue_id"]]
        sev = Counter(r["severity"] for r in m)
        detail.append(row | {"topic": row["issue_id"].split(".")[0],
                             "cancellation_count": sum(r["intent"] == "cancellation" for r in m),
                             "needs_review_count": sum(bool(r["needs_review"]) for r in m),
                             **{f"sev{k}": sev.get(k, 0) for k in range(1, 6)}})
    completed = [r for r in records if r.get("status") == "completed"]
    n_completed = len(completed)
    topics = []
    groups = {t: [t] for t in TOPICS} | {"billing_support": ["billing", "support"]}
    for name, members_t in groups.items():
        rs = [r for r in completed if r["topic"] in members_t]
        comp = [r for r in rs if r["intent"] in RANKED_INTENTS]
        s = sum(r["severity"] for r in comp)
        topics.append({"area": name, "completed_records": len(rs), "complaint_records": len(comp),
                       "cancellation_records": sum(r["intent"] == "cancellation" for r in rs),
                       "severity_sum": s, "mean_severity": mean6(s, len(comp)) if comp else "0.000000",
                       "sev4plus_complaints": sum(r["severity"] >= 4 for r in comp),
                       "share_of_all_complaints": pct(len(comp), sum(1 for r in completed if r["intent"] in RANKED_INTENTS)),
                       "share_of_completed": pct(len(rs), n_completed)})
    return detail, topics


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fields})


RANK_FIELDS = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]
AGG_FIELDS = RANK_FIELDS + ["topic", "cancellation_count", "needs_review_count", "sev1", "sev2", "sev3", "sev4", "sev5"]
TOPIC_FIELDS = ["area", "completed_records", "complaint_records", "cancellation_records", "severity_sum",
                "mean_severity", "sev4plus_complaints", "share_of_all_complaints", "share_of_completed"]


def run_from_files(out_dir: Path, records_path: Path = None, membership_path: Path = None, write=True):
    records_path = records_path or (out_dir / "enriched.jsonl")
    membership_path = membership_path or (out_dir / "membership.csv")
    records = read_jsonl(records_path)
    membership = read_membership(membership_path)
    ranked = ranking(records, membership)
    detail, topics = aggregates(records, membership, ranked)
    if write:
        write_csv(out_dir / "ranking.csv", ranked, RANK_FIELDS)
        write_csv(out_dir / "aggregates.csv", detail, AGG_FIELDS)
        write_csv(out_dir / "topic_aggregates.csv", topics, TOPIC_FIELDS)
    return ranked, detail, topics
