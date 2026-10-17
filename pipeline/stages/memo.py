"""Stage 6 - Recommend (MEMO role).

The memo model receives only saved aggregates, a list of citable claims (computed by code from
ranking.csv) and a bounded evidence pack. Code then checks every cited claim ID, every number
and every review ID; a rejected memo is retried once with the specific problem, then saved with
its validation failures visible. claims.csv keeps exactly the claims the memo cites.
"""

import hashlib
import json
import re

from ..config import prompt_text
from . import rank as rank_stage
from .common import cached_call

UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
CLAIM = re.compile(r"\[(C\d{3})\]")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
METRICS = ("complaint_count", "severity_sum", "mean_severity", "priority_score")


def memo_config(cfg):
    m = cfg.memo
    return f"{m.model}+effort-{m.effort}+memo-{m.prompt_version}"


def build_inputs(cfg, ranked, issues, topics, records, run_facts):
    names = {i["issue_id"]: i for i in issues}
    top = ranked[: cfg.memo_top_issues]
    claims, n = [], 0
    for row in top:
        for metric in METRICS:
            n += 1
            claims.append({"claim_id": f"C{n:03d}", "issue_id": row["issue_id"], "metric": metric, "value": str(row[metric])})
    by_issue = {}
    for r in records:
        if r.get("status") == "completed" and r["intent"] in rank_stage.RANKED_INTENTS:
            by_issue.setdefault(f"{r['topic']}.{r['subtopic']}", []).append(r)
    evidence = []
    for row in top[:6]:
        recs = sorted(by_issue.get(row["issue_id"], []),
                      key=lambda r: (r.get("cache_source_id") is not None, -r["severity"], r["needs_review"],
                                     hashlib.sha256(("memo-v1:" + r["review_id"]).encode()).hexdigest()))
        for r in recs[: cfg.memo_evidence_per_issue]:
            evidence.append({"issue_id": row["issue_id"], "review_id": r["review_id"], "intent": r["intent"],
                             "severity": r["severity"], "quote": r["evidence_quote"][:220]})
    payload = {
        "RUN_FACTS": run_facts,
        "RANKED_ISSUES": [{"rank": r["rank"], "issue_id": r["issue_id"], "name": names.get(r["issue_id"], {}).get("name"),
                           "summary": names.get(r["issue_id"], {}).get("summary"),
                           "coherence": names.get(r["issue_id"], {}).get("coherence")} for r in top],
        "CLAIMS": claims,
        "TOPIC_AGGREGATES": topics,
        "EVIDENCE": evidence,
    }
    return payload


def allowed_numbers(payload):
    vals = set()
    def add(v):
        s = str(v)
        vals.add(s)
        vals.add(s.replace(",", ""))
        if re.fullmatch(r"\d+", s):
            vals.add(f"{int(s):,}")
    for c in payload["CLAIMS"]:
        add(c["value"])
    for t in payload["TOPIC_AGGREGATES"]:
        for k, v in t.items():
            if k != "area":
                add(v)
    for v in payload["RUN_FACTS"].values():
        if isinstance(v, (int, float, str)):
            for m in NUMBER.findall(str(v)):
                add(m)
    for r in payload["RANKED_ISSUES"]:
        add(r["rank"])
    return vals


def check_memo(memo, payload):
    text = memo.get("memo_markdown", "")
    problems = []
    claim_ids = {c["claim_id"]: c for c in payload["CLAIMS"]}
    cited = CLAIM.findall(text)
    unknown = sorted(set(cited) - set(claim_ids))
    if unknown:
        problems.append(f"unknown claim IDs {unknown}")
    if not cited:
        problems.append("no claim IDs cited")
    evidence_ids = {e["review_id"] for e in payload["EVIDENCE"]}
    bad_ids = sorted(set(UUID.findall(text)) - evidence_ids)
    if bad_ids:
        problems.append(f"review IDs not in EVIDENCE {bad_ids[:3]}")
    allowed = allowed_numbers(payload)
    stripped = UUID.sub(" ", CLAIM.sub(" ", text))
    stripped = re.sub(r"^#+ .*$", " ", stripped, flags=re.M)            # headings
    stripped = re.sub(r"\b\w+\.\w+\b", " ", stripped)                  # issue ids like playback.crashes_wont_open
    unsupported = sorted({m for m in NUMBER.findall(stripped)
                          if m.rstrip("%") not in allowed and m not in allowed and m.replace(",", "") not in allowed})
    if unsupported:
        problems.append(f"numbers not in claims/aggregates {unsupported[:8]}")
    for cid in set(cited) & set(claim_ids):
        c = claim_ids[cid]
        # the cited value must appear in the same sentence/line as its claim tag
        for line in text.splitlines():
            nums = {m.replace(",", "").rstrip("%") for m in NUMBER.findall(CLAIM.sub(" ", line))}
            if f"[{cid}]" in line and c["value"] not in nums:
                problems.append(f"{cid} cited without its value {c['value']}")
                break
    if not re.search(r"(?i)limitation", text):
        problems.append("missing Limitations section")
    return problems, sorted(set(cited) & set(claim_ids))


def run(ctx, ranked, issues, topics, records, run_facts):
    cfg = ctx.cfg
    payload = build_inputs(cfg, ranked, issues, topics, records, run_facts)
    (ctx.out / "memo_inputs.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    instructions = prompt_text("memo", cfg.memo.prompt_version)
    payload_text = json.dumps(payload, ensure_ascii=False)

    def validate(p):
        problems, _ = check_memo(p, payload)
        return "; ".join(problems) if problems else None
    memo, src = cached_call(ctx, "memo", cfg.memo, instructions, payload_text, [], memo_config(cfg),
                            artifacts=["out/ranking.csv", "out/topic_aggregates.csv", "out/issues.json", "out/memo_inputs.json"],
                            validate=validate)
    status = "validated"
    if memo is None:
        status = f"failed:{src}"
        memo = {"priority_area": None, "headline": "Memo generation failed validation", "memo_markdown": ""}
    problems, cited = check_memo(memo, payload)
    claims = [c for c in payload["CLAIMS"] if c["claim_id"] in cited]
    rank_stage.write_csv(ctx.out / "claims.csv", claims, ["claim_id", "issue_id", "metric", "value"])
    header = (f"# Decision memo — Spotify review pipeline\n\n"
              f"_Run `{ctx.run_id}` · generated by `{cfg.memo.model}` (effort `{cfg.memo.effort}`, prompt memo-{cfg.memo.prompt_version}) "
              f"from saved aggregates only · code validation: **{'passed' if not problems else 'FAILED: ' + '; '.join(problems)}** · "
              f"claim IDs resolve in `claims.csv`._\n\n**{memo.get('headline', '')}**\n\n")
    (ctx.out / "memo.md").write_text(header + memo.get("memo_markdown", ""))
    result = {"status": status, "source": src, "validation_problems": problems, "cited_claims": cited, **memo}
    (ctx.out / "memo.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    ctx.log("memo", "done", status=status, source=src, problems=problems, cited_claims=len(cited))
    return result
