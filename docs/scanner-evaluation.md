# Scanner evaluation contract

The evaluation workflow tests frozen scanner selections. It is not a trading
backtest and does not tune the score from the same sample it reports.

## Declared conventions

`EvaluationConfig` is serialized into every report together with a stable
fingerprint. The default contract is:

- 20, 60, 120, and 250 open-market-day horizons;
- entry at the supplied close on the scan `as_of_date` and exit at the close on
  the Nth subsequent market date;
- candidate and benchmark use the same entry and exit dates, including when a
  candidate is suspended;
- each scan date is an independent, overlapping, equal-weight Top-N cohort;
- hit rate means a positive candidate return;
- turnover is Top-N Jaccard turnover between consecutive scan dates;
- transaction cost is a declared round-trip basis-point deduction;
- a security delisted inside the holding window receives the declared
  `delisted_return` only when a dated delisting reference proves the event;
- all other missing or failed observations remain missing with a reason code.

The report contains candidate and cohort return, net return, benchmark excess
return, median, hit rate, price-path and cohort drawdown, candidate/sample
counts, coverage, missingness, turnover, industry weights, market-cap
distribution, and fundamental-improvement evidence.

## Required research inputs

The scan Parquet files should be produced by `replay`, `replay-variants`, or
`scan`. New artifacts carry `snapshot_id`, `run_id`, score configuration
fingerprint, and frozen historical-universe membership on every selected row.

Evaluation also consumes:

- `daily`, containing candidates and the declared benchmark code;
- historical `stock_basic`, including listed, delisted, and pre-listing rows,
  `list_date`, and `delist_date` where applicable;
- dated `daily_basic` exposure rows, especially `total_mv`;
- an optional PIT fundamental-feature history supplied with `--fundamentals`.

The fundamental history must include `ts_code`, an availability field such as
`actual_available_date` or `f_ann_date`, a report period, and the declared
metrics. The default metrics are `revenue_yoy`, `net_profit_yoy`, and
`operating_profit_yoy`. The first subsequently available report inside the
holding window is compared with the frozen scan value (or the last PIT baseline
available at the scan date). Price return and fundamental improvement remain
separate outcomes.

Daily reference synchronization fetches `stock_basic` for `L`, `D`, and `P`
statuses. A security whose current status is `D` remains eligible in a replay
strictly before its dated delisting. Missing dates or historical membership do
not silently turn into a clean observation.

## Reproducible command

```bash
python -m ashare_turnaround evaluate \
  --scans data/derived/replays/replay-20250630-fundamental_only.parquet \
  --data-dir data \
  --benchmark-code 000300.SH \
  --horizons 20 60 120 250 \
  --fundamentals data/derived/research/fundamental-history.parquet \
  --report data/reports/evaluation-fundamental_only.json
```

The JSON report stores the full configuration, limitations, input digests,
source snapshot/run ids, summaries, and reason-coded observations. `PASS` means
all declared evidence was available. `PARTIAL` is an intentional research
result when a benchmark window, historical-universe proof, exposure, price, or
fundamental observation is incomplete.

## Limitations

Return quality depends on the supplied close series and its corporate-action
adjustment. Overlapping cohorts are not a capital-constrained live portfolio.
Static industry fallback values are identified as such. The framework reports
evidence; it does not claim tradability, future performance, or investment
suitability.
