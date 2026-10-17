"""Stage 3 - Verify (VERIFY role).

A separate model task re-labels a declared deterministic random sample WITHOUT seeing the
enrichment answer (it receives only the original text + rubric). Code compares the two and
writes a disagreement report. Sampling: sha256(seed:review_id) < rate, so membership is
stable, reproducible and independent of the labels.
"""

import hashlib
import json
from collections import Counter

from ..config import prompt_text
from ..labels import definitions_markdown, split_label
from .common import cached_call


def in_sample(review_id, seed, rate):
    h = int(hashlib.sha256(f"{seed}:{review_id}".encode()).hexdigest()[:12], 16)
    return h / 16 ** 12 < rate


def verify_config(cfg):
    v = cfg.verify
    return f"{v.model}+effort-{v.effort}+verify-{v.prompt_version}"


def verify_texts(ctx, texts, artifacts=None):
    """texts: list of (review_id, text). Returns {review_id: verifier_item}. Shared by the
    pipeline stage and the planted-error / injection evals. Only text is sent, never labels."""
    cfg = ctx.cfg
    instructions = prompt_text("verify", cfg.verify.prompt_version).replace("{{DEFINITIONS}}", definitions_markdown())
    results, size = {}, min(cfg.verify.batch_size, 50)
    for i in range(0, len(texts), size):
        chunk = texts[i:i + size]
        payload = json.dumps([{"i": k + 1, "text": t} for k, (_, t) in enumerate(chunk)], ensure_ascii=False)
        parsed, src = cached_call(ctx, "verify", cfg.verify, instructions, payload, [rid for rid, _ in chunk],
                                  verify_config(cfg), artifacts=artifacts)
        if parsed is None:
            ctx.log("verify", "batch_failed", n=len(chunk), error=src)
            if src == "spend_limit":
                break
            continue
        for it in parsed.get("items", []):
            k = it.get("i")
            if isinstance(k, int) and 1 <= k <= len(chunk) and chunk[k - 1][0] not in results:
                results[chunk[k - 1][0]] = it
    return results


def run(ctx):
    cfg, con = ctx.cfg, ctx.store.con
    # Population: records labelled by a model call in any run (exact-text reuses add no new judgement).
    rows = con.execute("SELECT r.review_id, v.review_text FROM results r JOIN reviews v USING(review_id) "
                       "WHERE r.cache_source_id IS NULL ORDER BY v.src_order").fetchall()
    sample = [(rid, t) for rid, t in rows if in_sample(rid, cfg.verify_seed, cfg.verify_rate)]
    vcfg = verify_config(cfg)
    ctx.log("verify", "start", population=len(rows), declared_rate=cfg.verify_rate, sample=len(sample))
    res = verify_texts(ctx, sample, artifacts=["out/verify/"])
    with ctx.store.tx() as c:
        for rid, it in res.items():
            topic, sub = split_label(it["label"])
            c.execute("INSERT OR REPLACE INTO verify VALUES(?,?,?,?,?,?,?,?,?)",
                      (rid, it["label"], topic, sub, it["intent"], it["severity"], it.get("confidence"), None, vcfg))

    # Code compares; the verifier never saw the first prediction.
    comp = con.execute("""SELECT v.review_id, r.topic, v.topic, r.subtopic, v.subtopic, r.intent, v.intent,
                                 r.severity, v.severity, v.confidence, rv.review_text
                          FROM verify v JOIN results r USING(review_id) JOIN reviews rv USING(review_id)
                          WHERE v.verify_config=? ORDER BY rv.src_order""", (vcfg,)).fetchall()
    n = len(comp)
    dis, conf, preds = [], Counter(), []
    for rid, et, vt, es, vs, ei, vi, esv, vsv, vconf, text in comp:
        conf[(et, vt)] += 1
        preds.append({"review_id": rid, "topic": vt, "subtopic": vs, "intent": vi, "severity": vsv, "confidence": vconf})
        fields = [f for f, a, b in (("topic", et, vt), ("intent", ei, vi), ("severity", esv, vsv)) if a != b]
        if fields:
            dis.append({"review_id": rid, "disagree": fields,
                        "enrich": {"topic": et, "subtopic": es, "intent": ei, "severity": esv},
                        "verify": {"topic": vt, "subtopic": vs, "intent": vi, "severity": vsv, "confidence": vconf},
                        "text": text[:400]})

    def rate(pred):
        return round(sum(1 for c in comp if pred(c)) / n, 4) if n else None
    summary = {
        "verify_config": vcfg, "declared_rate": cfg.verify_rate, "seed": cfg.verify_seed,
        "sampling": "sha256(seed + ':' + review_id) / 16^12 < declared_rate over model-labelled records",
        "population_model_labelled": len(rows), "sample_size": len(sample), "verified": n,
        "unverified_in_sample": len(sample) - n,
        "topic_agreement": rate(lambda c: c[1] == c[2]), "intent_agreement": rate(lambda c: c[5] == c[6]),
        "severity_exact": rate(lambda c: c[7] == c[8]),
        "severity_within_1": rate(lambda c: abs(c[7] - c[8]) <= 1),
        "severity_mae": round(sum(abs(c[7] - c[8]) for c in comp) / n, 4) if n else None,
        "disagreements": len(dis),
        "topic_confusion": [{"enrich": a, "verify": b, "count": k} for (a, b), k in sorted(conf.items(), key=lambda x: (-x[1], x[0]))],
    }
    vdir = ctx.out / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "verify_summary.json").write_text(json.dumps(summary, indent=2))
    (vdir / "disagreements.jsonl").write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in dis))
    (vdir / "verifier_predictions.jsonl").write_text("".join(json.dumps(p) + "\n" for p in preds))
    ctx.log("verify", "done", **{k: v for k, v in summary.items() if k != "topic_confusion"})
    return summary
