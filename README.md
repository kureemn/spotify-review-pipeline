# Spotify Reviews → Product Priority: a multi-agent data pipeline

**Live dashboard:** https://spotify-review-insights-smoky.vercel.app (public, no login) · **Decision memo:** [`memo.md`](⟨TBD memo path⟩) ·
**Grading export:** [`grading/`](grading/) · **Cost calculator:** [`cost/report.md`](cost/report.md) · **Architecture:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

Berkeley Agentic AI, final assignment. A staged pipeline classifies **all 660,622** Google Play reviews of Spotify
(May 2022 – Nov 2023), ranks product issues with code-only arithmetic, and writes a recommendation whose every number
traces to a saved calculation. A reviewer can inspect and recompute everything below **without an API key**.

> ⟨TBD: one-paragraph answer: priority area, top issues, key numbers with claim IDs⟩

## Results summary (final full-corpus run `full-v1`)

| | Value | Evidence |
|---|---|---|
| Source rows accounted for | ⟨TBD⟩ / 660,622 | `grading/records.jsonl.gz`, checker report |
| Nonempty reviews classified (valid) | ⟨TBD⟩ / 660,609 | ⟨TBD⟩ |
| Quarantined | 13 empty texts + ⟨TBD⟩ unresolved | `runs/full-v1/out/quarantine.jsonl` (release asset) |
| Exact-text reuse | ⟨TBD⟩ records reuse a direct original (`cache_source_id`) | checker `valid_cache_reuses` |
| Verifier (blind, declared 2% sample) | ⟨TBD⟩ | `runs/full-v1/out/verify/` |
| Golden-50 vs human labels | ⟨TBD⟩ | `evals/golden/golden_report.md` |
| Full-run API cost (usage × rates) | ⟨TBD⟩ (cap $9.50 enforced in code) | `runs/full-v1/out/run_summary.json` |
| Full-run wall clock | ⟨TBD⟩ | run log |
| Measured 100-review pilot | cold $0.003343 / 38.6 s · warm 0 calls / $0 / 0.55 s | [`cost/report.md`](cost/report.md) |

Measured values and estimates are labelled separately throughout. "Accounted for" (completed **or** quarantined) is not
the same as "classified" (valid completed records).

## Rubric → evidence map

### Deliverable quality
| Criterion | Where to look |
|---|---|
| Accessible code, setup, artifacts | [Setup](#setup) · [Commands](#commands-paid-calls-always-need---confirm-paid) · pinned [`requirements.txt`](requirements.txt) · [`.env.example`](.env.example) · offline checks in [Verify without an API key](#verify-without-an-api-key) |
| Architecture, shared schema, provenance | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (diagram + stage contracts) · [`docs/LABELS.md`](docs/LABELS.md) · [`pipeline/labels.py`](pipeline/labels.py) · `runs/*/run_manifest.json` (source sha, code sha, prompt shas, model/tier) · `label_config` on every record · direct `cache_source_id` provenance |
| Memo numbers ↔ calculations ↔ evidence | memo cites `claims.csv` IDs; [`pipeline/stages/memo.py`](pipeline/stages/memo.py) rejects any unknown claim, number or review ID (the 10k run's first draft was rejected and fixed on retry: see its `run_log.jsonl`); checker recomputes claims |
| Recommendation, alternatives, limitations | [Decision memo](#decision-memo) · trend analysis with denominators · [Limitations](#limitations) |

### Testing & evaluation
| Criterion | Where to look |
|---|---|
| 50 human labels, per-field comparison, error analysis | `golden/golden_50_labeled.csv` · [`evals/golden/golden_report.md`](evals/golden/golden_report.md) · protocol **predeclared before labelling** in [`docs/RUBRIC_PLAN.md`](docs/RUBRIC_PLAN.md) |
| Independent verification, planted-error and injection tests | blind verifier (text + rubric only): `runs/full-v1/out/verify/` · planted errors: [`evals/planted/`](evals/planted/) (30/30 gross) and [`evals/planted_subtle/`](evals/planted_subtle/) (57/60 near-miss) · injection: [`evals/injection/`](evals/injection/) (5/5 resisted by both roles, 15 ordinary reviews in the same batch unaffected) |
| Real 100-review cold/warm pilot, offline calculator, retry/spend/recovery controls | [`cost/report.md`](cost/report.md) · `python3 cost/calculator.py selftest` · controls demonstrated in [Controls in action](#controls-in-action) |

### Working result
| Criterion | Where to look |
|---|---|
| Full ingestion, coverage, classification | `grading/ingestion.json` (byte-identical to the course profile) · checker output in [Verify without an API key](#verify-without-an-api-key) |
| Runnable staged program, bounded calls, saved handoffs, resume | `python -m pipeline run --input <csv>` · ≤ 50 reviews per enrichment request · `runs/<id>/out/` handoffs · **real interrupt + resume of the graded run**: [recording](docs/evidence/resume_demo.rec), `grading/checkpoint_before.json` / `checkpoint_after.json` |
| Reproducible ranking + deployed dashboard / backend / database | `python -m pipeline rank --from runs/full-v1/out` (byte-identical) · [live dashboard](https://spotify-review-insights-smoky.vercel.app) → Vercel functions ([`dashboard/api/`](dashboard/api/)) → Neon Postgres ([`dashboard/db/`](dashboard/db/)) |

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
| Rerank from saved outputs | `.venv/bin/python -m pipeline rank --from runs/full-v1/out` | no |
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
.venv/bin/python -m pipeline rank --from runs/full-v1/out     # needs the enriched.jsonl release asset in runs/full-v1/out/
python3 cost/calculator.py selftest
script -p docs/evidence/resume_demo.rec                        # replays the recorded interrupt/resume in real time
```

⟨TBD: checker result summary⟩

## Controls in action

⟨TBD: table of real outcomes — Ctrl-C drain, resume with 0 re-sent IDs, retries recovered, spend ledger, insufficient_quota handling, mock fault injection⟩

## One review, traced end to end

⟨TBD: real review ID → enrichment record → verification → issue membership → ranking row → memo claim⟩

## A failed / ambiguous case and its handling

⟨TBD⟩

## Decision memo

⟨TBD: memo or link + claim table⟩

## Baseline ranking, aggregation rules, scope

- Baseline: completed `complaint` + `cancellation` records only; one issue per review (`allow_multi_issue: false`);
  `issue_id = topic.subtopic`; `priority_score = severity_sum = complaint_count × mean_severity`; order by descending
  score, then ascending `issue_id`; means as six-decimal half-up strings.
- Praise, requests and unclear records are excluded from the ranking but appear in area aggregates.
- Trend analysis compares each area's share of all complaints per calendar month, with denominators. The first and last
  months (2022-05, 2023-11) are partial.

## Limitations

⟨TBD⟩

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
