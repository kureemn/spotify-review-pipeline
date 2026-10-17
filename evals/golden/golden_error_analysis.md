# Golden-50 error analysis

**Predictions:** final full run `runs/full-v1`, whose config was frozen before the golden labels were compared.
**Reference:** `golden/golden_50_labeled.csv`, human labels made blind (text only, star rating hidden) and committed in
`6d8695f` before this evaluation ran. Metrics follow the protocol predeclared in [`docs/RUBRIC_PLAN.md`](../../docs/RUBRIC_PLAN.md);
full tables are in [`golden_report.md`](golden_report.md) and per-case results in [`golden_cases.jsonl`](golden_cases.jsonl).

| Topic | Intent | Severity exact | Severity MAE | Joint | Sentiment within ±0.5 | Quote exact substring | Missing / quarantined |
|---|---|---|---|---|---|---|---|
| 74% | 80% | 72% | 0.32 | 52% | 96% | 100% | 0 |

The human marked 2 cases ambiguous. They wrote notes but no alternative labels, so strict and accepted-set agreement are
identical. **No prompt, threshold or example was changed after seeing these results.** Fifty reviews are a diagnostic
sample, not a population accuracy estimate.

## Disagreements by pattern (24 of 50 differ on at least one field)

Each case is assigned to the one pattern that explains it, judged against the shared definitions in
`GRADING_CONTRACT.md`. "Rule favours" says which label the written definitions support. That is the analyst's reading; the
human labels themselves are left unchanged.

| # | Pattern | Cases | Rule favours | What it means |
|---|---|---|---|---|
| A | **Premium-only controls → billing.** The model labels "can't choose, skip or repeat songs for free" as `billing.free_tier_restrictions`; the human labelled the symptom (usability or playback). | `991b6b3a`, `3ddb3f4e`, `8c0546b0`, `dceb14e7` | model (contract: "explicitly premium-only controls go here") | The biggest topic driver, and it matters for the business answer: it moves volume from usability and playback into billing. The memo reports it as a definitional boundary. |
| B | **Short or non-English text: "unclear" vs an inferred intent.** The model read Hinglish/Indonesian or very short reviews as complaints or praise; the human used "unclear". | `01d453b0`, `46842184`, `372d4e67`, `1d15724a` ("Good"), `2aa566c6`, `16d640e9` | mostly model (contract: unclear = meaningless or uninterpretable; "Good" is praise). `46842184` (Indonesian lyrics complaint) is genuinely hard; the model flagged it `needs_review`. | Model behaviour is consistent with the definitions. The human leaned on "unclear" when unsure. |
| C | **Boycott statements.** `5de7f95b` "We are boycotting Swedish app…": human complaint, model unclear. `8cc4fad4` "I hate this app and sweden": human unclear, model complaint. | `5de7f95b`, `8cc4fad4` | split: the contract says bare boycott slogans are unclear unless a product complaint or personal departure is expressed. "boycotting … app" can be read as departure; "I hate this app" counts as a generic complaint. | A real ambiguity in the definitions. The model flagged `5de7f95b` "boycott protest statement". |
| D | **Severity 3 vs 4 for "can't play songs".** | `47f1406d`, `215463ae`, `723f07de` | model for `215463ae` ("can't even open"); human for `723f07de`, which says the problem was **resolved** after contacting support. The model rated the old failure 4 but did flag "resolved after support". | Severity 4 needs a clearly blocked core task. Resolution status and partial blocking cause off-by-one disagreements. |
| E | **Over-escalation to severity 5.** `46c0b49f` "my ears feel like they explode" about a loud ad. The model gave 5. | `46c0b49f` | human (5 is reserved for explicit financial, privacy or data harm; angry language alone is insufficient) | **A genuine model error.** The only two-level severity miss. |
| F | **Praise topic specificity.** For praise of recommendations or "no ads", the model picks the specific feature; the human picked `other`. | `de48c99b`, `5fba12ea`, `1fc8b08f` | model (contract: "for a positive review choose the first specific praised feature") | Definition-consistent model behaviour. Praise has severity 1, so the ranking is unaffected. |
| G | **Likely human slips.** `0c6ca545` "Hate this application 🤮" was labelled praise/1. | `0c6ca545` | model (complaint) | Kept as labelled. It is an example of why a single human pass is not ground truth. |
| H | **Pure request vs annoyance; content objection severity.** `b58dd6f0` (request: human severity 2, model 1); `292ce26a` (catalog content objection: human severity 2, model 3); `99b93e31` (topic other vs usability.ui_redesign for "this update very bad"). | `b58dd6f0`, `292ce26a`, `99b93e31` | `b58dd6f0`: model (pure request = 1). `292ce26a`: human (objection to content, no functional loss = 2). `99b93e31`: either (an update complaint is in ui_redesign's scope, but no specific defect is named). | Small boundary disagreements. |
| — | Other single-field differences | `947821dc` (usability vs playback for "song plays a little then stops"; rule favours playback: a playback failure) | model | |

**Reading the patterns:** of the 24 disagreements, the written rules favour the model in about 15, the human in about 4
(D partially, E, `292ce26a`), and about 5 are genuinely ambiguous (C, `46842184`, `99b93e31`, `723f07de`). The one
clear model error with business impact is severity over-escalation (E). It is rare in the full run: severity-5 records
are a small share of the ranked complaints (see `runs/full-v1/out/aggregates.csv`, column `sev5`). The systematic
boundary that matters most for the recommendation is pattern A. It is a deliberate rule in the shared taxonomy, and the
memo's limitations call it out.

## Does the quote support the label?

Code checks exact-substring membership (50/50 pass). Whether each quote *supports* its label is judged by the human
labeller (pending): [`golden_quote_review.csv`](golden_quote_review.csv).

## needs_review as a prediction

The model flagged 9 of 50 for review; the human flagged 2 as ambiguous. The overlap is 1 (precision 0.11, recall 0.50). The
flag is over-inclusive: it fires on politically charged or non-English text, which the human did not consider ambiguous.
It is a routing signal, not a correctness guarantee.

## Entities

Entity extraction is code-only (a regex lexicon), so it cannot invent a term absent from the text. All differences are
lexicon matches the human did not list (e.g. `ads` ×8, `update` ×7). The human listed no entity the code missed.

## What this does and does not show

- It shows the pipeline applies the shared boundary rules more literally than a single quick human pass (patterns A, B, F),
  plus one real severity over-escalation pattern (E).
- It does not establish population accuracy: 50 cases, one labeller, and a sample dominated by short praise.
- Independent corroboration at scale comes from the blind verifier on the full run's declared 2% sample (9,707 reviews):
  see `runs/full-v1/out/verify/verify_summary.json`.
