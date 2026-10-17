# Spotify Reviews → Product Priority: a multi-agent data pipeline

**Live dashboard:** https://spotify-review-insights-smoky.vercel.app (public, no login) · **Decision memo:** [`runs/full-v1/out/memo.md`](runs/full-v1/out/memo.md) ·
**Grading export:** [`grading/`](grading/) · **Cost calculator:** [`cost/report.md`](cost/report.md) · **Architecture:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

Berkeley Agentic AI, final assignment. A staged pipeline classifies **all 660,622** Google Play reviews of Spotify
(May 2022 – Nov 2023), ranks product issues with code-only arithmetic, and writes a recommendation whose every number
traces to a saved calculation. A reviewer can inspect and recompute everything below **without an API key**.

> **Answer:** put next quarter's effort into **billing / support, starting with the free-tier restrictions**.
> "Can't choose, skip or repeat songs without Premium" is the top-ranked issue: **52,477 complaints [C001]**, severity sum
> **150,324 [C002]**, mean severity **2.864569 [C003]**. In October 2023 it pushed billing/support to **40.9% of all complaints**
> (27,062 of 66,168), up from 12.3% (989 of 8,019) in October 2022. Ads (39,367 complaints [C009]) are the adjacent second
> workstream. Access (login failure, mean severity 3.897855 [C027]) and playback stops (3.136948 [C023]) are more severe per
> review but much smaller. These are self-selected public reviews: expressed intent, not observed churn or revenue.

## Results summary (final full-corpus run `full-v1`)

| | Value | Evidence |
|---|---|---|
| Source rows accounted for | **660,622 / 660,622** (no missing, duplicate or foreign IDs) | `grading/records.jsonl.gz` · checker `accounted_fraction 1.0` |
| Nonempty reviews classified (valid) | **660,471 / 660,609 (99.979%)** | checker `labelable_completion_fraction 0.999791`, coverage point candidate **0.9999** |
| Quarantined | **151** = 13 `empty_review_text` + **138** `invalid_output_after_retry` (see [failure case](#a-failed--ambiguous-case-and-its-handling)) | `quarantine.jsonl` (release asset) |
| Exact-text reuse | **176,413** records reuse a direct original (`cache_source_id`); 484,189 distinct texts | checker `valid_cache_reuses 176413` |
| Verifier (blind, declared 2% sample, n = 9,707) | topic **88.0%**, intent **94.8%**, severity exact **88.8%** / within ±1 **99.5%**, MAE 0.117 | [`runs/full-v1/out/verify/`](runs/full-v1/out/verify/) |
| Golden-50 vs human labels | topic **74%**, intent **80%**, severity exact **72%** (MAE 0.32), joint 52%, sentiment within ±0.5 **96%**, quotes 100% exact | [`evals/golden/golden_error_analysis.md`](evals/golden/golden_error_analysis.md) |
| Full-run API cost (usage × rates) | **$5.46** (12,733 calls, 12,731 on the flex tier); **$5.57 for the whole project** including pilot, dev runs and tests, under the $9.50 code cap and the $10 course cap | [`runs/full-v1/out/run_summary.json`](runs/full-v1/out/run_summary.json) |
| Full-run wall clock | **3.3 h** across 3 enrichment sessions (10 workers): initial → recorded Ctrl-C → resume → content-filter stop → fixed → resume | `runs/full-v1/out/run_log.jsonl` |
| Cost forecast vs actual | the calculator's base projection (refreshed at 10k) was **$5.19**; the full run cost **$5.46 (+5%)**, inside the base–conservative range ($5.19–$14.73) | [`cost/report.md`](cost/report.md) · `run_summary.json` |
| Measured 100-review pilot | cold $0.003343 / 38.6 s · warm 0 calls / $0 / 0.55 s | [`cost/report.md`](cost/report.md) |

**Large files:** `enriched.jsonl` (660,471 validated predictions with subtopic and provenance, 74 MB gzipped) is a
[GitHub release asset](https://github.com/kureemn/spotify-review-pipeline/releases/tag/v1.0-full-run). Everything the checker needs is committed in `grading/`. Hashes for every output are in
[`runs/full-v1/data_manifest.json`](runs/full-v1/data_manifest.json).

Measured values and estimates are labelled separately throughout. "Accounted for" (completed **or** quarantined) is not
the same as "classified" (valid completed records).

## Rubric → evidence map

### Deliverable quality
| Criterion | Where to look |
|---|---|
| Accessible code, setup, artifacts | [Setup](#setup) · [Commands](#commands-paid-calls-always-need---confirm-paid) · pinned [`requirements.txt`](requirements.txt) · [`.env.example`](.env.example) · offline checks in [Verify without an API key](#verify-without-an-api-key) |
| Architecture, shared schema, provenance | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (diagram + stage contracts) · [`docs/LABELS.md`](docs/LABELS.md) · [`pipeline/labels.py`](pipeline/labels.py) · `runs/*/run_manifest.json` (source sha, code sha, prompt shas, model/tier) · `label_config` on every record · direct `cache_source_id` provenance |
| Memo numbers ↔ calculations ↔ evidence | memo cites `claims.csv` IDs; [`pipeline/stages/memo.py`](pipeline/stages/memo.py) rejects any unknown claim, number or review ID (the 10k run's first draft was rejected and fixed on retry: see its `run_log.jsonl`); checker recomputes claims |
| Recommendation, alternatives, limitations | [Decision memo](#decision-memo) (reviewed and approved by the author, below) · trend with denominators: [`monthly_complaint_share.csv`](runs/full-v1/out/monthly_complaint_share.csv) · [Limitations](#limitations) |

### Testing & evaluation
| Criterion | Where to look |
|---|---|
| 50 human labels, per-field comparison, error analysis | [`golden/golden_50_labeled.csv`](golden/golden_50_labeled.csv) (committed before evaluation) · [`golden_report.md`](evals/golden/golden_report.md) (agreement, macro F1, confusion tables) · [`golden_error_analysis.md`](evals/golden/golden_error_analysis.md) (patterns, quote-support inspection, needs_review as a predictor) · per-case [`golden_cases.jsonl`](evals/golden/golden_cases.jsonl) · protocol **predeclared** in [`docs/RUBRIC_PLAN.md`](docs/RUBRIC_PLAN.md) |
| Independent verification, planted-error and injection tests | blind verifier (text + rubric only): `runs/full-v1/out/verify/` · planted errors: [`evals/planted/`](evals/planted/) (30/30 gross) and [`evals/planted_subtle/`](evals/planted_subtle/) (57/60 near-miss) · injection: [`evals/injection/`](evals/injection/) (5/5 resisted by both roles, 15 ordinary reviews in the same batch unaffected) |
| Real 100-review cold/warm pilot, offline calculator, retry/spend/recovery controls | [`cost/report.md`](cost/report.md) · `python3 cost/calculator.py selftest` · controls demonstrated in [Controls in action](#controls-in-action) |

### Working result
| Criterion | Where to look |
|---|---|
| Full ingestion, coverage, classification | `grading/ingestion.json` (byte-identical to the course profile) · checker output in [Verify without an API key](#verify-without-an-api-key) |
| Runnable staged program, bounded calls, saved handoffs, resume | `python -m pipeline run --input <csv>` · ≤ 50 reviews per enrichment request · `runs/<id>/out/` handoffs · **real interrupt + resume of the graded run**: timed recording [`resume_demo.rec`](docs/evidence/resume_demo.rec) (macOS `script -p`), readable [transcript](docs/evidence/resume_demo_transcript.txt), `grading/checkpoint_before.json` / `checkpoint_after.json` |
| Reproducible ranking + deployed dashboard / backend / database | `python -m pipeline rank --from rerank --records grading/records.jsonl.gz --membership grading/membership.csv` (byte-identical, 8 s, no model) · [live dashboard](https://spotify-review-insights-smoky.vercel.app) → Vercel functions ([`dashboard/api/`](dashboard/api/)) → Neon Postgres ([`dashboard/db/`](dashboard/db/)) |

## Architecture (summary)

Six stages run in a fixed order under code control: **ingest → enrich → verify → group → rank → recommend**. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the diagram, each stage's input/output/failure/stop contract, and why each
model call exists.

| Role | Model / settings | Sees | Produces |
|---|---|---|---|
| enrich | `gpt-6-luna`, `reasoning.effort=none`, flex tier, strict JSON schema, ≤ 50 reviews per request | review text only | label, intent, severity, sentiment, quote, review flag |
| verify | same model, separate prompt | text + rubric (never the first answer) | blind labels for a declared 2% sample |
| group | same model, separate prompt | ≤ 8 code-sampled quotes per issue | issue names, coherence flags |
| memo | same model, separate prompt | code-made aggregates, claims, bounded evidence | recommendation (validated by code) |

Code does everything else: dedupe, caching, batching, ID and quote validation, entity extraction, retries, quarantine,
atomic saves, spending caps, membership, ranking arithmetic and claim checking.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # add OPENAI_API_KEY locally for paid runs only — never commit .env
```

Data: download the course dataset ZIP and unzip it into `data/`. The 97.4 MB `spotify_reviews_18months.csv` is not
committed. SHA-256 `1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6`. Source: BwandoWando,
*3.4 Million Spotify Google Store Reviews* v2 (Kaggle, CC0).

## Commands (paid calls always need `--confirm-paid`)

| Purpose | Command | Paid? |
|---|---|---|
| Full staged pipeline on **any CSV** | `.venv/bin/python -m pipeline run --input <csv> --run-id <id> --provider openai --confirm-paid --workers 10 --spend-limit 7.5` | yes |
| Same pipeline, offline mock provider | `.venv/bin/python -m pipeline run --input data/cost_100.csv --run-id mock100 --provider mock` | no |
| Run status / resume evidence | `.venv/bin/python -m pipeline status --run-id <id>` | no |
| Rerank from saved outputs | `.venv/bin/python -m pipeline rank --from rerank --records grading/records.jsonl.gz --membership grading/membership.csv` | no |
| Grading export | `.venv/bin/python -m pipeline export-grading --run-id full-v1` | no |
| Cost calculator (offline replay, default) | `python3 cost/calculator.py` · `python3 cost/calculator.py selftest` | no |
| Measured pilot (cold, then warm) | `.venv/bin/python -m pipeline pilot --mode cold --provider openai --confirm-paid` | yes |
| Golden evaluation | `python3 evals/golden_eval.py --labels golden/golden_50_labeled.csv --run runs/full-v1` | no |
| Planted-error / injection tests | `.venv/bin/python -m evals.system_tests planted --source-run runs/dev500 --provider openai --confirm-paid` | yes (cents) |
| Load dashboard DB | `.venv/bin/python dashboard/db/load.py --run runs/full-v1` (needs `DATABASE_URL`) | no |

## Verify without an API key

```bash
python3 data/check_submission.py reference --full data/spotify_reviews_18months.csv --analysis data/spotify_reviews_18months.csv --out local-reference.json
python3 data/check_submission.py check --reference local-reference.json --submission grading --out self-check.json
.venv/bin/python -m pipeline rank --from rerank --records grading/records.jsonl.gz --membership grading/membership.csv   # byte-identical to grading/ranking.csv
cmp rerank/ranking.csv grading/ranking.csv
python3 cost/calculator.py selftest
script -p docs/evidence/resume_demo.rec                        # replays the recorded interrupt/resume in real time
```

Checker result on the submitted `grading/` folder (saved outside it as [`evals/self_check_full_run.json`](evals/self_check_full_run.json)): `status: review_required` with a single
issue, **`unfinished_classification: 138`**, the honestly reported quarantines. Ranking, claims, cache reuse, call logs,
label configs, quote membership and resume evidence all pass. 12,964 calls are reported (the full run plus the
dev-checkpoint calls whose results it reused by review ID).

## Controls in action

| Control | Real outcome (graded run unless noted) | Evidence |
|---|---|---|
| Interrupt + resume | A real SIGINT at 12:24:54 drained the 10 in-flight batches and saved **138,483** completed. The resume started from that count, and **0** already-completed IDs were re-sent | [`docs/evidence/resume_demo.rec`](docs/evidence/resume_demo.rec) (`script -p` replays it) · `grading/checkpoint_before.json` / `checkpoint_after.json` |
| Retry once on invalid output | Item-level failures (quote not an exact substring, omitted items) were retried once. 322 saved with the full text as the quote plus `needs_review` | `run_log.jsonl` batch events (`failure_reasons`) |
| Transient errors | Bounded backoff with jitter (≤ 4 retries per request). No rate limiting occurred at 10 workers on flex | `calls` table |
| Provider content filter | One batch was rejected with HTTP 400 and **stopped session 2** (a bug: every 400 was fatal). Fixed to bisect the batch down to the offending review and quarantine only that review. Tested offline, then the run resumed. No rejection recurred | commit `87fc271` · [failure case](#a-failed--ambiguous-case-and-its-handling) |
| Spend ledger | Worst-case cost reserved before every dispatch; run cap $7.50, project cap $9.50 across all runs. Spent $5.46 / $5.57 | `run_log.jsonl` `budget` events |
| Credit exhaustion | `insufficient_quota` (HTTP 429) is fatal rather than retried | `pipeline/llm.py` |
| Fault injection (offline) | Mock provider: transient errors, malformed JSON, dropped items, fabricated quotes, duplicate JSON, content flags. All recovered or quarantined with reasons; the checker passed except for deliberate quarantines | `scripts/mock_resume_test.sh` |

## One review, traced end to end

Review **`116ebe7a-8a4e-46f2-8a40-e53f9598dee1`** (2022-05-28, 1 star: metadata only, never shown to a model):
*"Shuffle play is forced, when you wanna listen to a specific song without premium, you can't"*

1. **Source → ingest:** row hash `5f55b7a2…` (course `row_sha`), queued as pending; its text is distinct, so no reuse.
2. **Enrich:** sent in request `req_5646723dd6ae49cb9a7c93a721adfffe` (≤ 50 reviews, text only), validated to
   `billing.free_tier_restrictions` · `complaint` · severity **3** · sentiment −1.0. The review is 91 characters, so the
   evidence quote is the full text (an exact substring). Entities from the code lexicon: `premium`, `shuffle`.
3. **Verify:** in the declared 2% hash sample. The blind verifier independently returned `billing.free_tier_restrictions`
   · complaint · 3 (confidence high) → **agreement** (`runs/full-v1/out/verify/verifier_predictions.jsonl`).
4. **Group:** member of issue `billing.free_tier_restrictions` (`grading/membership.csv`). The group role named the issue
   and judged it coherent.
5. **Rank:** contributes 1 to `complaint_count` and 3 to `severity_sum` of the **rank-1** issue (52,477 · 150,324 ·
   2.864569 in `grading/ranking.csv`).
6. **Memo:** claims **C001 / C002 / C003** in [`claims.csv`](grading/claims.csv) cite those exact values. The checker
   recomputes them from records + membership.

## A failed / ambiguous case and its handling

**Failure: OpenAI's content filter stopped the full run (session 2).** At 303,869 completed, one request was rejected with
HTTP 400 *"prompt flagged as potentially violating our usage policy"*. Some reviews in this corpus are abusive or
political. The pipeline classified every non-retryable HTTP 400 as fatal, so the whole run stopped. It stopped safely: all
saved work was intact and nothing was re-sent later.
**Recorded decision:** a content rejection is a property of one review, not of the run. The batch is now bisected
(50 → 25 → … → 1) without spending the invalid-output retry, and only a review rejected on its own is quarantined
(`provider_content_filter_rejected`). Fatal attempts are now written to the call log too; the original rejected attempt
predates that fix and is missing from `calls`. Tested offline with a planted flagged review (120/121 classified, 6 extra
requests), then the run resumed; the rejection did not recur.

**Failure: 138 reviews quarantined after their single retry** (`invalid_output_after_retry`). These are ordinary reviews
(e.g. `1d56fb59…` "Very nice and good!") that sat in a batch whose output was **truncated twice**. The model occasionally
loops; 14 of about 9,000 batches hit the output cap even at 6,000 tokens (about 240 tokens per review vs the usual 32). The
first 56 were quarantined under the original policy (one whole-batch retry) and 75 after the fix (the retry split into
halves, which still truncated); duplicate texts bring the total to 138. **Decision:** keep them quarantined and visible
rather than give them an undeclared second retry. Impact: 0.02% of nonempty reviews; it lowers coverage, not correctness.

**Ambiguous case:** golden review `5de7f95b` *"We are boycotting Swedish app in our protest against Sweden…"*. The human
labelled it complaint, the model unclear (flagged `needs_review: boycott protest statement`). The contract makes bare
boycott slogans `unclear` unless a product complaint or personal departure is expressed. "Boycotting … app" can be read
either way, so it is reported as genuinely ambiguous ([error analysis](evals/golden/golden_error_analysis.md), pattern C)
and both labels are kept. At corpus scale, July 2023's boycott wave (48,907 complaint/cancellation records that month,
only 4.5% billing) inflates `other.general` (rank 2). The memo treats it as not actionable.

## Decision memo

**Human review:** the author read the memo against the saved ranking, claims and evidence and approved the argument
on 2026-10-17. The caveat about the premium-only billing boundary is added in [Limitations](#limitations).

Full text: [`runs/full-v1/out/memo.md`](runs/full-v1/out/memo.md), generated by the memo role from saved aggregates only
and validated by code (claim IDs, every number, every review ID). It is also on the live dashboard, where each claim ID
shows its stored value. Material claims:

| Claim | Issue | Metric | Value |
|---|---|---|---|
| C001 | billing.free_tier_restrictions | complaint_count | 52,477 |
| C002 | billing.free_tier_restrictions | severity_sum | 150,324 |
| C003 | billing.free_tier_restrictions | mean_severity | 2.864569 |
| C009 | usability.ads | complaint_count | 39,367 |
| C017 / C019 | usability.shuffle_queue_controls | complaint_count / mean_severity | 15,716 / 2.841690 |
| C021 / C023 | playback.stops_skips_lag | complaint_count / mean_severity | 12,837 / 3.136948 |
| C025 / C027 | access.login_failure | complaint_count / mean_severity | 8,018 / 3.897855 |

Area-level context (code-computed, `topic_aggregates.csv`): usability has the largest share of complaints (31.4%) but
mostly low severity (ads, redesign); billing/support has 20.9% overall and dominates the latest complete months (40.9% in
October 2023); playback is 12.0% with the most severity ≥ 4 complaints (10,177); access is small (3.2%) but the most
severe (mean 3.854471). Alternatives are compared in the memo itself.

## Baseline ranking, aggregation rules, scope

- Baseline: completed `complaint` + `cancellation` records only; one issue per review (`allow_multi_issue: false`);
  `issue_id = topic.subtopic`; `priority_score = severity_sum = complaint_count × mean_severity`; order by descending
  score, then ascending `issue_id`; means as six-decimal half-up strings.
- Praise, requests and unclear records are excluded from the ranking but appear in area aggregates.
- Trend analysis compares each area's share of all complaints per calendar month, with denominators. The first and last
  months (2022-05, 2023-11) are partial.

## Limitations

- **Review bias:** these are self-selected public Google Play reviews, so the volume follows events (e.g. the July 2023
  boycott wave, the September–October 2023 free-tier change). There is no revenue, plan tier or confirmed churn data.
  Cancellation language is stated intent.
- **Taxonomy boundary drives the headline:** the shared rule sends explicitly premium-only controls to `billing`. The golden
  analysis (pattern A) shows a human labeller often files the same reviews under usability or playback. Under the other
  reading, the free-tier volume would sit in usability. The issue (#1) stays the same; the *area* label depends on the rule.
- **Label quality:** the blind verifier agrees 88% on topic. The golden-50 sample is small, from one labeller, and mostly
  short praise. Known model error: rare severity-5 over-escalation (0.74% of ranked complaints are severity 5). Some
  issues are flagged `mixed` or `mislabeled` by the group role (e.g. `usability.ui_redesign`). Totals are still exact
  counts of the assigned labels.
- **Incomplete classification:** 138 nonempty reviews are quarantined (0.02%). They are excluded from aggregates and counted
  as incorrect in any benchmark.
- **Trend:** the first and last calendar months are partial; month-to-month volume swings by 10×, so shares (with
  denominators) are reported, not raw counts.
- **Costs** are usage × the published rates on the dates checked (`cost/rates.csv`), not a provider invoice.

## Repository map

| Path | Contents |
|---|---|
| `pipeline/` | orchestrator, stages, prompts, shared labels, providers |
| `runs/<id>/` | per-run manifest and saved handoffs (`out/`); large tables are release assets |
| `grading/` | standardized export for `check_submission.py` |
| `cost/` | calculator, rates, pilot usage and calls, report |
| `evals/` | golden evaluation, planted-error and injection tests |
| `golden/` | blind labelling page and the human-labelled golden set |
| `dashboard/` | Vercel functions, Neon schema and loader, UI |
| `docs/` | architecture, labels, rubric plan, evidence recording |
| `data/` | course files (the full CSV is not committed) |
