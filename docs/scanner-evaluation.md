# Scanner evaluation framework

The shared `scanner.evaluation` module (`evaluation-v3`) evaluates frozen scanner
selections. It is not a trading backtest and never tunes the score from the
same observations it reports. The calibrated campaign freezes the more specific
`baseline-evaluation-contract-v1`.

## Declared conventions

`EvaluationConfig` is serialized with a stable fingerprint. The baseline
configuration is:

- Top-20 formal rows with `ranking_eligible=true`;
- 20, 60, 120, and 250 open SSE `trade_cal` sessions;
- entry at the scanner `as_of` close;
- exit at the close on the Nth strictly subsequent session;
- benchmark `000300.SH` / CSI 300 from `index_daily`;
- stock return from exact endpoint `close × adj_factor` values;
- benchmark return from raw index close on the same endpoint dates;
- 30 bps fixed round-trip total transaction-cost deduction;
- independent, overlapping, equal-weight cohort summaries;
- dated delisting inside a window receives the declared `delisted_return`;
- all other unavailable observations remain in the output with a reason code.

The output keeps separate `market_outcomes` and `fundamental_outcomes` tables.
The older `observations` table remains a compatibility view; it is not a joint
score or label.

## Required research inputs

Scan Parquet files should be produced by `replay`, `scan`, or the lightweight
Issue #32 projection. A baseline row carries `snapshot_id`, `run_id`, score
configuration fingerprint, contract versions, and `ranking_eligible`.

The evaluator consumes:

- `daily` candidate prices;
- `index_daily` for the fixed CSI 300 benchmark;
- open-session `trade_cal`;
- `adj_factor` for stock endpoint adjustment;
- dated `stock_basic` list/delist reference and `daily_basic.total_mv` exposure;
- optional `fina_indicator`-derived future fundamental history.

Current `stock_basic.industry` is not used as a historical baseline fallback;
Issue #32 marks that state `UNSUPPORTED_PIT`. Industry is reported only when it
comes from a frozen scan or dated exposure row.

## Price-adjustment gate

The baseline uses:

```text
adjusted_close = close × adj_factor
adjusted_return = adjusted_close_exit / adjusted_close_entry - 1
```

Both exact endpoints require a finite positive factor. Missing or ambiguous
factors produce `missing_adjustment_factor_*`, not a raw-close fallback. CSI
300 is explicitly raw index level because no index adjustment-factor corpus is
available. Synthetic split/dividend boundary tests protect this contract.

## Fundamental follow-through

`build_fundamental_history()` creates an evaluation-only projection from
`fina_indicator`. It preserves report period, availability date, and disclosure
version. The evaluator selects the first two **distinct report periods** whose
initial availability is after the scanner snapshot; later revisions are
recorded but are not used. It reports Revenue YoY, Profit YoY, margin,
CFO/cash conversion, next-report follow-through, next-two-report persistence,
and false-turnaround status with metric-level missing reasons.

Future outcome rows cannot flow back into selection, ranking, score, or config.
Missing reports and metrics are unavailable evidence, not failures.

## Reproducible commands

Generic evaluation remains available:

```bash
python -m ashare_turnaround evaluate \
  --scans data/derived/replays/replay-20250630-fundamental_only.parquet \
  --data-dir data --benchmark-code 000300.SH \
  --horizons 20 60 120 250 --fundamentals data/derived/research/fundamental-history.parquet \
  --report data/reports/evaluation.json
```

The frozen campaign uses one shared engine and a checkpointed lightweight
snapshot projection:

```bash
python -m ashare_turnaround baseline-evaluate \
  --schedule data/reports/issue32-target-schedule/validation-targets.json \
  --artifact-root <Issue-32-local-artifact-root> \
  --data-dir data --output data/reports/baseline-evaluation-campaign \
  --report data/reports/baseline-evaluation.json
```

`PASS`/`PARTIAL` describes outcome availability and input completeness, not a
claim that the Scanner has economic alpha. The report always includes input
digests, provenance, coverage, missingness, and limitations.

## Scope boundary

This framework does not implement Feature Ablation, Score v2 selection, weight
or threshold tuning, Top-N search, holding-policy optimization, transaction-cost
sensitivity, benchmark search, or live trading. Exit/holding rules such as
three-month exit, next-quarter exit, and take-profit belong to a later,
separately frozen study.
