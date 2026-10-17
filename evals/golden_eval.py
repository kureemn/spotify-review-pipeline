"""Golden-50 evaluation (protocol predeclared in docs/RUBRIC_PLAN.md). No model calls.

    python3 evals/golden_eval.py --labels golden/golden_50_labeled.csv --run runs/full-v1

Compares the human labels with the saved predictions of a run (by review_id). Missing or
quarantined predictions count as incorrect. "Accepted-set" metrics also accept labels the human
listed as acceptable for ambiguous cases (format: "topic=usability; severity=3,4; intent=complaint"),
mirroring the instructor checker's accepted_* lists. Writes evals/golden/*.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")
FIELDS = ("topic", "intent", "severity")
SENTIMENT_TOLERANCE = 0.5   # predeclared


def parse_alt(text):
    acc = defaultdict(set)
    for part in re.split(r"[;\n]", text or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip().lower()
            for x in re.split(r"[,/| ]+", v.strip()):
                if x:
                    acc[k].add(int(x) if k == "severity" and x.isdigit() else x.lower())
    return acc


def load_predictions(run: Path):
    out = run / "out"
    preds = {}
    for name in ("enriched.jsonl", "quarantine.jsonl"):
        p = out / name
        if p.exists():
            for line in open(p, encoding="utf-8"):
                r = json.loads(line)
                preds[r["review_id"]] = r
    return preds


def f1_table(pairs, labels):
    scores = {}
    for lab in labels:
        tp = sum(a == lab and p == lab for a, p in pairs)
        sup = sum(a == lab for a, _ in pairs)
        pred = sum(p == lab for _, p in pairs)
        if sup:
            scores[lab] = {"support": sup, "predicted": pred, "precision": round(tp / pred, 4) if pred else 0.0,
                           "recall": round(tp / sup, 4), "f1": round(2 * tp / (sup + pred), 4)}
    return {"per_class": scores, "macro_f1": round(sum(s["f1"] for s in scores.values()) / len(scores), 4) if scores else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=Path("golden/golden_50_labeled.csv"))
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("evals/golden"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    gold = list(csv.DictReader(open(a.labels, encoding="utf-8-sig", newline="")))
    preds = load_predictions(a.run)
    incomplete = [g["review_id"] for g in gold if not (g["topic"] and g["intent"] and g["severity"])]
    if incomplete:
        raise SystemExit(f"{len(incomplete)} golden rows lack human labels; finish labelling first: {incomplete[:3]}")

    cases, pairs = [], defaultdict(list)
    tot = Counter()
    sev_abs, sev_abs_acc, sent_abs = [], [], []
    nr_pred_human = []
    ent_extra, ent_missed = Counter(), Counter()
    conf = {f: Counter() for f in FIELDS}
    for g in gold:
        rid, text = g["review_id"], g["review_text"]
        exp = {"topic": g["topic"], "intent": g["intent"], "severity": int(g["severity"])}
        alt = parse_alt(g.get("also_acceptable"))
        accepted = {f: {exp[f]} | alt.get(f, set()) for f in FIELDS}
        ambiguous = g.get("ambiguous") == "true" or any(len(v) > 1 for v in accepted.values())
        p = preds.get(rid)
        valid = bool(p) and p.get("status") == "completed"
        row = {"review_id": rid, "text": text, "ambiguous": ambiguous, "prediction_status": (p or {}).get("status", "missing"),
               "quarantine_reason": (p or {}).get("reason")}
        for f in FIELDS:
            pv = p.get(f) if valid else None
            row[f"expected_{f}"], row[f"accepted_{f}"], row[f"predicted_{f}"] = exp[f], sorted(accepted[f], key=str), pv
            row[f"{f}_strict"] = pv == exp[f]
            row[f"{f}_accepted"] = pv in accepted[f]
            tot[f + "_strict"] += row[f"{f}_strict"]
            tot[f + "_accepted"] += row[f"{f}_accepted"]
            conf[f][(str(exp[f]), str(pv))] += 1
            if len(accepted[f]) == 1:
                pairs[f].append((str(exp[f]), str(pv) if pv is not None else "<abstain>"))
        row["joint_strict"] = all(row[f"{f}_strict"] for f in FIELDS)
        row["joint_accepted"] = all(row[f"{f}_accepted"] for f in FIELDS)
        tot["joint_strict"] += row["joint_strict"]
        tot["joint_accepted"] += row["joint_accepted"]
        tot["ambiguous"] += ambiguous
        tot["abstain"] += not valid
        if valid:
            sev_abs.append(abs(p["severity"] - exp["severity"]))
            sev_abs_acc.append(min(abs(p["severity"] - x) for x in accepted["severity"]))
            if g.get("sentiment") not in (None, ""):
                d = abs(float(p["sentiment"]) - float(g["sentiment"]))
                sent_abs.append(d)
                row["sentiment_expected"], row["sentiment_predicted"], row["sentiment_within_tol"] = float(g["sentiment"]), p["sentiment"], d <= SENTIMENT_TOLERANCE
            q = p["evidence_quote"]
            row["quote_exact_substring"] = q in text
            row["quote_predicted"], row["quote_human"] = q, g.get("evidence_quote", "")
            hq = g.get("evidence_quote") or ""
            row["quote_overlaps_human"] = bool(hq) and (hq in q or q in hq)
            human_ents = set(json.loads(g["entities"] or "[]")) if g.get("entities", "").startswith("[") else set()
            pe = set(p.get("entities", []))
            row["entities_predicted"], row["entities_human"] = sorted(pe), sorted(human_ents)
            ent_extra.update(pe - human_ents)
            ent_missed.update(human_ents - pe)
            human_flag = g.get("needs_review") == "true" or g.get("ambiguous") == "true"
            nr_pred_human.append((bool(p.get("needs_review")), human_flag))
            row["needs_review_predicted"], row["needs_review_human"] = bool(p.get("needs_review")), human_flag
            row["model_note"] = p.get("review_note")
            row["predicted_subtopic"] = p.get("subtopic")
        cases.append(row)

    n = len(gold)
    tp = sum(pr and h for pr, h in nr_pred_human)
    summary = {
        "labels_file": str(a.labels), "run": str(a.run), "cases": n, "ambiguous_cases": tot["ambiguous"],
        "missing_or_quarantined_predictions": tot["abstain"],
        "agreement_strict": {f: round(tot[f + "_strict"] / n, 4) for f in (*FIELDS, "joint")},
        "agreement_accepted_set": {f: round(tot[f + "_accepted"] / n, 4) for f in (*FIELDS, "joint")},
        "macro_f1_singleton": {f: f1_table(pairs[f], sorted({x for x, _ in pairs[f]})) for f in FIELDS},
        "severity_exact": round(tot["severity_strict"] / n, 4),
        "severity_mae_vs_primary": round(sum(sev_abs) / len(sev_abs), 4) if sev_abs else None,
        "severity_mae_vs_nearest_accepted": round(sum(sev_abs_acc) / len(sev_abs_acc), 4) if sev_abs_acc else None,
        "sentiment_mae": round(sum(sent_abs) / len(sent_abs), 4) if sent_abs else None,
        "sentiment_within_tolerance": round(sum(d <= SENTIMENT_TOLERANCE for d in sent_abs) / len(sent_abs), 4) if sent_abs else None,
        "sentiment_tolerance_predeclared": SENTIMENT_TOLERANCE,
        "quote_exact_substring_rate": round(sum(c.get("quote_exact_substring", False) for c in cases) / n, 4),
        "quote_support_judgement": "human: fill quote_supports_label in golden_quote_review.csv (supports / partial / does_not)",
        "entities_predicted_not_in_human": dict(ent_extra.most_common()),
        "entities_human_not_predicted": dict(ent_missed.most_common()),
        "needs_review_as_predictor": {"predicted": sum(pr for pr, _ in nr_pred_human), "human_flagged": sum(h for _, h in nr_pred_human),
                                      "true_positive": tp,
                                      "precision": round(tp / max(sum(pr for pr, _ in nr_pred_human), 1), 4),
                                      "recall": round(tp / max(sum(h for _, h in nr_pred_human), 1), 4)},
        "confusion": {f: [{"expected": e, "predicted": p, "count": k} for (e, p), k in sorted(conf[f].items(), key=lambda x: -x[1])]
                      for f in FIELDS},
        "note": "50 cases are a small diagnostic sample, not a population accuracy estimate.",
    }
    (a.out / "golden_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    (a.out / "golden_cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False, default=str) + "\n" for c in cases))
    with open(a.out / "golden_quote_review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["review_id", "predicted_topic", "predicted_intent", "predicted_severity", "quote_predicted", "quote_supports_label", "comment"])
        for c in cases:
            w.writerow([c["review_id"], c["predicted_topic"], c["predicted_intent"], c["predicted_severity"], c.get("quote_predicted", ""), "", ""])
    write_md(a.out / "golden_report.md", summary, cases)
    print(json.dumps({k: summary[k] for k in ("cases", "ambiguous_cases", "missing_or_quarantined_predictions",
                                              "agreement_strict", "agreement_accepted_set", "severity_mae_vs_primary", "sentiment_mae")}, indent=1))


def table(conf_rows, labels):
    m = defaultdict(int)
    for r in conf_rows:
        m[(r["expected"], r["predicted"])] = r["count"]
    preds = sorted({r["predicted"] for r in conf_rows} | set(labels), key=lambda x: (x not in labels, labels.index(x) if x in labels else 0, x))
    exps = [l for l in labels if any(r["expected"] == l for r in conf_rows)]
    out = ["| expected \\ predicted | " + " | ".join(preds) + " |", "|---" * (len(preds) + 1) + "|"]
    out += [f"| **{e}** | " + " | ".join(str(m[(e, p)] or "·") for p in preds) + " |" for e in exps]
    return out


def write_md(path, s, cases):
    L = ["# Golden-50 evaluation", "", f"Run `{s['run']}` vs human labels `{s['labels_file']}` · {s['cases']} cases · "
         f"{s['ambiguous_cases']} ambiguous · {s['missing_or_quarantined_predictions']} missing/quarantined (counted incorrect)", "",
         "| metric | topic | intent | severity | joint |", "|---|---|---|---|---|",
         "| agreement (strict) | " + " | ".join(f"{s['agreement_strict'][f]:.0%}" for f in (*FIELDS, "joint")) + " |",
         "| agreement (accepted set) | " + " | ".join(f"{s['agreement_accepted_set'][f]:.0%}" for f in (*FIELDS, "joint")) + " |",
         "| macro F1 (unambiguous cases) | " + " | ".join(str(s["macro_f1_singleton"][f]["macro_f1"]) for f in FIELDS) + " | |", "",
         f"Severity: exact {s['severity_exact']:.0%}, MAE {s['severity_mae_vs_primary']} (vs nearest accepted {s['severity_mae_vs_nearest_accepted']}). "
         f"Sentiment: MAE {s['sentiment_mae']}, within predeclared ±{s['sentiment_tolerance_predeclared']}: {s['sentiment_within_tolerance']}. "
         f"Quote exact-substring rate: {s['quote_exact_substring_rate']:.0%}.", "",
         f"needs_review as a predictor of human ambiguity flags: {s['needs_review_as_predictor']}", "",
         f"Entities predicted but not listed by the human: {s['entities_predicted_not_in_human']}; listed by human but not predicted: {s['entities_human_not_predicted']}", "",
         "## Topic confusion", ""] + table(s["confusion"]["topic"], list(TOPICS)) + ["", "## Intent confusion", ""] + \
        table(s["confusion"]["intent"], list(INTENTS)) + ["", "## Severity confusion", ""] + table(s["confusion"]["severity"], ["1", "2", "3", "4", "5"]) + \
        ["", "## Disagreements (strict, any field)", "", "| review_id | text | expected (t/i/s) | predicted (t/i/s) | accepted? |", "|---|---|---|---|---|"]
    for c in cases:
        if not c["joint_strict"]:
            t = c["text"].replace("|", "/").replace("\n", " ")[:160]
            L.append(f"| `{c['review_id'][:8]}` | {t} | {c['expected_topic']}/{c['expected_intent']}/{c['expected_severity']} | "
                     f"{c['predicted_topic']}/{c['predicted_intent']}/{c['predicted_severity']} | {'yes' if c['joint_accepted'] else 'no'} |")
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
