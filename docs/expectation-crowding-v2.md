# Expectation / Crowding v2 — benchmark-relative contract (#30)

This is the frozen contract for `features.market.compute_crowding_features`.
It is a correctness contract, not a production replay or score-calibration
result.

## 1. Versioned identity

| Item | Frozen value |
| --- | --- |
| expectation/crowding contract | `expectation-crowding-v2` |
| benchmark contract | `benchmark-v1` |
| primary benchmark | `000300.SH` (CSI 300 Index) |
| benchmark source | `index_basic` definition + `index_daily` prices |
| price field | `close` |
| price adjustment | raw/unadjusted close exactly as stored; adjustment quality is not proven |

The benchmark is explicit.  This version does not select among CSI 500,
CSI 1000, industry benchmarks, or multiple benchmarks.  A combined `daily`
fixture containing benchmark rows is accepted only as a small synthetic
compatibility input; production replay loads the separate `index_daily`
frame.

The benchmark declaration is attached to feature, score, replay, and report
metadata and includes `benchmark_id`, `benchmark_name`, `version`,
`source_dataset`, `definition_dataset`, and `price_dataset`.

## 2. Excess-return arithmetic

For a common as-of market session `t` and `L` prior open sessions:

```text
R_stock(t,L)      = P_stock(t) / P_stock(t-L) - 1
R_benchmark(t,L)  = P_benchmark(t) / P_benchmark(t-L) - 1
excess_return(t,L)= R_stock(t,L) - R_benchmark(t,L)
```

The feature group emits both canonical names and the old honest aliases:

| Canonical feature | Compatibility alias | Meaning |
| --- | --- | --- |
| `stock_return_20d` | `recent_return_20d` | stock-only 20-session return |
| `benchmark_return_20d` | — | primary benchmark 20-session return |
| `excess_return_20d` | `recent_excess_return` | stock minus benchmark, 20 sessions |
| `stock_return_60d` | `momentum_60d` | stock-only 60-session return |
| `benchmark_return_60d` | — | primary benchmark 60-session return |
| `excess_return_60d` | — | stock minus benchmark, 60 sessions |

Every known return evidence record contains `start_session`, `end_session`,
`stock_start`, `stock_end`, `benchmark_start`, `benchmark_end`,
`benchmark_id`, `stock_return`, `benchmark_return`, and `excess_return` in its
components, along with the formula and configuration.  The source datasets
are `daily` and `index_daily`.

Example:

```text
stock:     100 -> 120  => +20%
benchmark: 100 -> 110  => +10%
excess:                  +10%
```

There is no path that assigns `stock_return` to an excess-return field.

## 3. Trading-session and suspension policy

`20D`, `60D`, and the high window count open market sessions, never calendar
days.  The axis is:

1. `trade_cal` rows with `is_open == 1`, when supplied and not stale; or
2. the union of valid stock/`index_daily` sessions when no usable calendar is
   supplied, so a missing endpoint cannot silently shift the lookback.

The anchor is the last axis session at or before `as_of`.  A weekend or holiday
therefore rolls to the previous open session for both series.  Endpoints must
be present on the exact same axis session; nearby dates are never substituted.

The stock suspension policy uses the market-session convention.  A missing
stock close at the anchor or at `t-L` returns `unknown`; a gap strictly inside
the endpoint window does not change endpoint arithmetic.  When `suspend_d` is
provided, its dated rows are treated as explicit missing stock observations.
This is explicit and versioned in `BenchmarkConfig.stock_suspension_policy`.

The following cases are fail-closed: missing benchmark, stale calendar or
benchmark, missing stock endpoint, missing benchmark endpoint, misaligned
session, insufficient history, invalid/zero price, and invalid numeric values.
They return `None` with an evidence reason (for a missing benchmark endpoint
this reason is `missing_benchmark_endpoint`).  In particular:

```text
missing benchmark -> excess_return = UNKNOWN
```

It never becomes an absolute stock return.

## 4. 52-week high

The default reference is the **prior 252 trading sessions**, excluding the
current session.  The current close is compared with that reference:

```text
distance_to_52w_high = current_close / max(prior_252_closes) - 1
high_proximity        = clamp(current_close / prior_high, 0, 1)
```

A new high can therefore have positive distance; a price below the high has a
negative distance.  At least 60 valid stock observations are required.  The
window start/end, market-session count, observation count, high, and current
price are preserved in evidence.  `insufficient_52w_history` is unknown, not a
neutral value.  `high_include_as_of` is configurable only through the versioned
benchmark declaration and defaults to `False`.

## 5. Crowding evidence and penalties

Recent repricing is separate from fundamental signals.  The default crowding
penalty uses these mandatory, known components:

```text
repricing_20d = clamp(max(excess_return_20d, 0) / 0.15, 0, 1)
repricing_60d = clamp(max(excess_return_60d, 0) / 0.30, 0, 1)
volume_spike  = vol(t) / median(vol over prior 60 sessions)
turnover_spike = turnover_rate(t) / median(turnover_rate over prior 60 sessions)
spike_penalty(x) = clamp(max(x - 1, 0) / (2.0 - 1.0), 0, 1)
```

`volume_spike` uses `daily.vol`; `turnover_spike` uses
`daily_basic.turnover_rate`.  The baseline is strictly before the anchor and
never contains current or future observations.  Missing baseline/current
values remain unknown.  `crowding_penalty` is the mean of the mandatory
penalties, expressed on a 0–100 scale, and `expectation_score` is
`100 - crowding_penalty`.  If a mandatory component is unknown, both aggregate
values are unknown rather than an average that silently rewards missing data.

Each penalty evidence record contains its metric, raw components, normalized
value/state, penalty, status/reason, formula/config, lookback and observation
sessions, source dataset, as-of, and benchmark metadata where applicable.
The risk flag `already_repriced_or_crowded` is a crowding flag only and fires at
the declared feature threshold of 70; it is not fundamental deterioration.

## 6. Valuation

Valuation is evidence-only by default.  The preferred field is positive
`daily_basic.pe_ttm`, with positive `pe` as a fallback.  The percentile is
`P(positive PE <= PE(t))` over the prior 252 sessions, with at least 20 valid
population observations.  The field, population, lookback, positive-only
policy, and observation count are recorded.  Missing, non-positive, or
insufficient valuation denominators are `unknown` (`valuation_non_positive`,
`valuation_unavailable`, or `valuation_insufficient_history`), never an
invented ordinary percentile.  Opting valuation into the penalty is an
explicit versioned configuration choice and does not change `ScoreConfig`.

## 7. Disclosure reaction

Only a disclosure with a provable availability date is considered:
`actual_date` is preferred and `ann_date` is the documented fallback.  The
selected availability date must be `<= as_of`.  The reaction consumes the first
five open sessions **strictly after** availability, and the final observation
must also be `<= as_of`.  Stock and benchmark endpoints use the same sessions
and the same excess-return arithmetic.  Missing timing, endpoint, or reaction
history is `unknown`; a later price can never alter an earlier as-of snapshot.
The reaction is evidence-only by default.

## 8. Fundamental separation and metadata

Financial features do not consume market prices.  Crowding features do not
consume financial statements.  A crowding penalty is not subtracted from
`fundamental_score`; the output retains separate fundamental evidence,
crowding evidence, and expectation penalty evidence.  `FeatureEvidence` stores
`semantic_version`, `formula`, `components`, and `config`.

The contract version and benchmark declaration are present in:

- each crowding feature vector and evidence record;
- `ScoreResult` and ranked score rows;
- `ReplayConfig`/`ReplayResult.metadata()` and replay artifacts;
- candidate report metadata and grouped evidence.

All calculations are deterministic for identical input frames.  No forward
returns, production replay, evaluation, ablation, score-weight tuning, or new
data download is part of this contract.
