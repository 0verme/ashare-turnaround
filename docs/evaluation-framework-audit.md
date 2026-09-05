# Evaluation Framework audit before baseline outcomes

Audit performed on the evaluation framework that existed before this branch.
`ALREADY_CORRECT` means the old behavior matched the required boundary;
`GAP` means it was changed minimally in the existing evaluator;
`UNSUPPORTED` means the old framework did not provide the capability.

| Area | Before | Status | Action in `evaluation-v3` / baseline contract |
| --- | --- | --- | --- |
| selection vs market outcome | `_selected_scans` excluded rejected rows but could consume diagnostic/ineligible rows; market and fundamental fields were emitted together | GAP | require `ranking_eligible` when present; retain separate market/fundamental tables and a compatibility view |
| benchmark identity | `benchmark_code` could be `None`; price came from the combined `daily` frame | GAP | freeze `000300.SH` / CSI 300, load `index_daily`, preserve missing benchmark evidence |
| benchmark convention | same target date arithmetic existed, but index source was not enforced | GAP | raw `index_daily.close`, exact common calendar endpoints, no absolute-return fallback |
| 20D/60D/120D/250D | horizons were integer counts after the union of dates in `daily` | GAP | use open SSE `trade_cal` sessions and the Nth strictly subsequent session |
| suspension | missing stock endpoint became a generic missing horizon; no explicit suspension reason | GAP | exact endpoint only; use `suspend_d` to label `suspended_at_exit`; never carry forward |
| delisting | dated delisting assumption existed, but treatment was embedded in the old candidate observation path | ALREADY_CORRECT (boundary) / GAP (schema) | retain dated `-1.0` assumption, expose status/reason and never use current status as history |
| transaction cost | default was 0 bps and code multiplied a field described as round-trip by 2 | GAP | freeze one 30 bps round-trip total deduction; net return is explicit and no sensitivity sweep is run |
| price adjustment | evaluator used raw `close` only and documented adjustment quality as a limitation | GAP | exact stock `close × adj_factor` endpoints; missing/ambiguous factor is unavailable, never raw fallback in baseline |
| benchmark adjustment | no adjustment-aware benchmark path | ALREADY_CORRECT (data limitation) / GAP (declaration) | freeze raw CSI 300 index close because the local index corpus has no `adj_factor`; identity is explicit |
| next report fundamental outcome | first available row inside the price horizon, not a report-period contract | GAP | select next distinct report period after T; record period/version/availability and use an evaluation-only branch |
| next two reports | unsupported | UNSUPPORTED | add next-two distinct report periods and persistence status |
| revision/restatement | availability was filtered, but later rows could win by availability and no selected version/evidence was returned | GAP | `first_available_version_after_snapshot`; later revisions counted/recorded, never used for selection |
| fundamental definitions | mean delta of three fields; no margin/CFO/false-turnaround contract | GAP | freeze Revenue YoY, Profit YoY, margin, CFO/cash conversion; strict-majority rule with minimum two metrics |
| future missingness | generic `fundamental_status` and summary drop-na counts | GAP | separate rows with `missing_report`, `missing_second_report`, `missing_metric`, and coverage/reason maps |
| historical universe | dated list/delist reference handling existed; current fallback could be used for exposure industry | GAP | preserve snapshot membership and forbid current `stock_basic.industry` fallback in baseline |
| exposure | dated market-cap fallback existed; industry could use current stock_basic | GAP | exact as-of exposure, deterministic market-cap terciles, frozen/dated industry only |
| provenance | config and input digests existed, but no adjustment/future-branch provenance | GAP | add calendar, index, adjustment digests, revision policy, separation and evaluation-only flags |
| snapshot/config provenance | replay rows carried IDs/fingerprints, but evaluator did not require/use them for campaign integrity | GAP | lightweight snapshot schema carries IDs, score/config fingerprints, contract versions, and campaign checkpoint |
| no second evaluator | old module was the shared evaluator and also contained ablation helper | ALREADY_CORRECT | extend the same `scanner.evaluation` module; baseline does not call `run_ablation` |

## Return-semantics gate

The local `adj_factor` corpus is present and is checked at exact stock
endpoints. A synthetic split/dividend boundary proves that the ratio of
adjusted endpoints removes a mechanical split move. The baseline command uses
`require_adjustment_factor=True`; it cannot produce a baseline alpha claim from
an unproven raw-close stock return. `index_daily` remains an explicitly raw
index-level convention.

If either the factor orientation or its endpoint coverage cannot be proven, the
campaign must stop with `EVALUATION_BLOCKED_BY_RETURN_SEMANTICS`; it must not
silently fall back to raw close.

## Audit conclusion

The old framework was not sufficient for the current #17 addendum. The minimal
repair is now versioned as `evaluation-v3` and frozen for the baseline by
`baseline-evaluation-contract-v1`. No Scanner feature formula, Score weight,
threshold, or ranking default was changed by this audit.
