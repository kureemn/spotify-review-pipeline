# Cost & runtime calculator

The calculator is [`calculator.py`](calculator.py). It uses only the standard library and makes **no API calls**.

| Purpose | Command | Key needed? |
|---|---|---|
| **Offline replay (default)**: recompute every charge from saved usage × `rates.csv`, then write `report.md` / `report.json` | `python3 cost/calculator.py` | no |
| What-if: all API rates doubled (usage fixed) | `python3 cost/calculator.py --rate-multiplier 2 --no-write` | no |
| Arithmetic invariants (doubling rates doubles API spend but not time or local cost; changing volume leaves measured results alone) | `python3 cost/calculator.py selftest` | no |
| Rebuild evidence files from saved run state | `python3 cost/calculator.py collect --cold runs/pilot-cold --warm runs/pilot-warm --dev runs/dev500 runs/dev10k` | no |
| **Paid pilot, cold** (empty result cache, 1 worker, `data/cost_100.csv` unchanged) | `.venv/bin/python -m pipeline pilot --mode cold --provider openai --confirm-paid --spend-limit 0.50` | **yes** |
| **Paid pilot, warm** (same settings, saved result cache → expect 0 new calls) | `.venv/bin/python -m pipeline pilot --mode warm --provider openai --confirm-paid --spend-limit 0.50` | yes (makes no calls if the cache is intact) |

## Files
- `rates.csv`: editable prices in their billing units, with source links and the date checked. Standard and flex tiers are listed separately.
- `assumptions.json`: editable budget, output-token cap, max workers, fallback fraction, retry rates and scenario settings.
- `pilot_calls.jsonl`: one row per attempted call (cold and warm), including failures and retries, with request IDs, stage, model, effort, tier, prompt version and raw usage.
- `pilot_records.jsonl`: one final record per pilot ID (grading-contract fields plus the warm-run status).
- `usage.csv`: one row per call × billing item (uncached input, cached input, cache write, output); every charged item maps to a `request_id` in the call log.
- `measurements.json`: wall clocks, stage times, counts, cache hits, settings.
- `report.md` / `report.json`: the measured-100 dashboard and the full-run projections.

## Formulas
`item_cost = billed_units × price_per_unit`, with the per-1M price divided by 1,000,000. The billing items never overlap: uncached input = input − cached − cache-write. Reasoning tokens are already part of output and are not added again. Each call is priced at the tier the provider reported using. Missing usage is shown as unresolved, never as zero. API spend is reported separately from local compute, which is marked unknown.
