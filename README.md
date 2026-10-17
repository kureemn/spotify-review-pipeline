# Spotify Reviews → Product Priority: a multi-agent data pipeline

> **Status (work in progress, 2026-10-16):** ingestion is complete for the full corpus. The pipeline has passed tests end-to-end with the offline mock provider. The paid 100-review pilot, the golden-set evaluation, the full run, the dashboard and the memo are **not done yet**. This README will map each rubric item to its evidence once they exist.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # then add OPENAI_API_KEY locally — never commit .env
```

Download the course dataset ZIP and unzip it into `data/`. The 97.4 MB `spotify_reviews_18months.csv` is not committed. Its SHA-256 is
`1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6`; the source is BwandoWando, *3.4 Million Spotify Google Store Reviews* v2 (Kaggle, CC0).

## Commands

| Purpose | Command | Paid? |
|---|---|---|
| Free offline end-to-end run (mock provider) | `.venv/bin/python -m pipeline run --input data/cost_100.csv --run-id mock100 --provider mock` | no |
| Fault-injection + interrupt/resume test | `scripts/mock_resume_test.sh` | no |
| Ingest/profile any CSV | `.venv/bin/python -m pipeline run --input <csv> --run-id <id> --stages ingest` | no |
| Measured 100-review pilot (cold, then warm) | `.venv/bin/python -m pipeline pilot --mode cold --provider openai --confirm-paid` | **yes** |
| Full staged pipeline on any CSV | `.venv/bin/python -m pipeline run --input <csv> --run-id <id> --provider openai --confirm-paid --spend-limit 5` | **yes** |
| Rerank from saved outputs | `.venv/bin/python -m pipeline rank --from runs/<id>/out` | no |
| Grading export | `.venv/bin/python -m pipeline export-grading --run-id <id>` | no |

Paid calls always require both `--provider openai` and `--confirm-paid`. Every run enforces a cumulative USD spending cap.

## Design (summary)

Six stages run in a fixed order under code control: **ingest → enrich → verify → group → rank → memo**.

- **Code** owns record accounting, exact-text reuse, batching (≤ 50 reviews per request), ID and quote validation, entity extraction, retries, quarantine, atomic per-batch saves, spending and time caps, membership, ranking arithmetic and claim checking.
- **Models** (`gpt-6-luna`, `reasoning.effort=none`, strict JSON schema) do four separate jobs:
  - enrich: label the review language;
  - verify: independently relabel a declared random sample without seeing the first answer;
  - group: name each issue and check it is coherent, without changing membership;
  - memo: write the recommendation from saved aggregates only.

The label definitions are in [docs/LABELS.md](docs/LABELS.md).
