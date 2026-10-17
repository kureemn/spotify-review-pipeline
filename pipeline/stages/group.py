"""Stage 4 - Group (GROUP role).

Code owns membership: each completed complaint/cancellation record joins exactly one issue,
issue_id = its topic.subtopic label (stable across runs, no model needed to re-rank).
The model only names/summarises each issue from a bounded deterministic example sample and
flags incoherent issues; it cannot change membership or numbers.
"""

import hashlib
import json

from ..config import prompt_text
from ..labels import SUBTOPICS, TOPIC_DEFINITIONS
from . import rank as rank_stage
from .common import cached_call


def group_config(cfg):
    g = cfg.group
    return f"{g.model}+effort-{g.effort}+group-{g.prompt_version}+k{cfg.group_examples}"


def _examples(records_by_issue, k, seed="group-v1"):
    """Deterministic bounded sample: hash order, model-labelled records first, distinct quotes."""
    out = {}
    for iid, recs in records_by_issue.items():
        recs = sorted(recs, key=lambda r: (r.get("cache_source_id") is not None,
                                           hashlib.sha256(f"{seed}:{r['review_id']}".encode()).hexdigest()))
        picked, quotes = [], set()
        for r in recs:
            q = r["evidence_quote"][:240]
            if q in quotes:
                continue
            quotes.add(q)
            picked.append({"review_id": r["review_id"], "quote": q})
            if len(picked) >= k:
                break
        out[iid] = picked
    return out


def run(ctx, records):
    cfg = ctx.cfg
    membership = rank_stage.build_membership(records)
    rank_stage.write_csv(ctx.out / "membership.csv", [{"issue_id": i, "review_id": r} for i, r in membership],
                         ["issue_id", "review_id"])
    by_id = {r["review_id"]: r for r in records}
    by_issue = {}
    for iid, rid in membership:
        by_issue.setdefault(iid, []).append(by_id[rid])
    examples = _examples(by_issue, cfg.group_examples)
    issue_ids = sorted(by_issue)
    instructions = prompt_text("group", cfg.group.prompt_version).replace("{{K}}", str(cfg.group_examples))
    named = {}
    size = max(1, cfg.group.batch_size)
    for i in range(0, len(issue_ids), size):
        chunk = issue_ids[i:i + size]
        payload = json.dumps({"issues": [{"issue_id": iid, "definition": _definition(iid), "examples": examples[iid]}
                                         for iid in chunk]}, ensure_ascii=False)
        allowed = {iid: {e["review_id"] for e in examples[iid]} for iid in chunk}

        def validate(p, allowed=allowed):
            got = [x.get("issue_id") for x in p.get("issues", [])]
            if sorted(got) != sorted(allowed):
                return f"issue_ids must be exactly {sorted(allowed)}"
            bad = [o for x in p["issues"] for o in x.get("outlier_ids", []) if o not in allowed[x["issue_id"]]]
            return f"unknown outlier_ids {bad[:3]}" if bad else None
        sent_ids = [e["review_id"] for iid in chunk for e in examples[iid]]
        parsed, src = cached_call(ctx, "group", cfg.group, instructions, payload, sent_ids, group_config(cfg),
                                  artifacts=["out/membership.csv"], validate=validate)
        for x in (parsed or {}).get("issues", []):
            named[x["issue_id"]] = x
        if parsed is None:
            ctx.log("group", "naming_failed", issues=chunk, error=src)

    issues = []
    for iid in issue_ids:
        n = named.get(iid, {})
        issues.append({"issue_id": iid, "topic": iid.split(".")[0], "subtopic": iid.split(".", 1)[1],
                       "name": n.get("name") or iid, "summary": n.get("summary", ""),
                       "coherence": n.get("coherence", "unnamed"), "outlier_ids": n.get("outlier_ids", []),
                       "example_ids": [e["review_id"] for e in examples[iid]],
                       "member_count": len(by_issue[iid]), "naming_source": "model" if n else "fallback_issue_id"})
    (ctx.out / "issues.json").write_text(json.dumps({
        "grouping_rule": "issue_id = topic.subtopic of each completed complaint/cancellation record; one issue per review",
        "group_config": group_config(cfg), "issues": issues}, indent=2, ensure_ascii=False))
    ctx.log("group", "done", issues=len(issues), members=len(membership),
            mixed=[i["issue_id"] for i in issues if i["coherence"] != "coherent"])
    return membership, issues


def _definition(iid):
    t, s = iid.split(".", 1)
    return f"{TOPIC_DEFINITIONS[t]} -> {SUBTOPICS[t][s]}"
