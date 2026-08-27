# Expectation / Crowding v2 — benchmark-relative contract (issue #30)

This document freezes the corrected Expectation/Crowding semantics. It is the
authoritative reference for the feature group implemented in
`features.market.compute_crowding_features` (semantic version `crowding-v2`)
and its benchmark resolution layer `features.benchmark` (semantic version
`benchmark-v1`).

## 1. Root cause fixed by this version

In the v1 implementation of issue #14, `recent_excess_return` was computed as
the stock's own 20-session close-to-close return:

```python
# v1 (WRONG): recent = _return(close, 20)   -> stock_return only
#define v1: recent_excess_return == P_stock(t) / P_stock(t-20) - 1
```

There was no benchmark anywhere in the computation, so the field was a plain
stock momentum value published under a benchmark-relative name. v2 removes
that semantic: the name `recent_excess_return` now means exactly

```text
excess return = stock return - benchmark return
```

over the same trading-session window, and the field is `unknown + reason`
whenever the benchmark cannot be resolved. The old (misnamed) value remains
visible only under its honest names `recent_return_20d` / `momentum_60d`,
which are stock-only returns and are never used as excess.

## 2. Benchmark decision

| Field | Value |
| --- | --- |
| benchmark_id | `000300.SH` |
| benchmark_name | CSI 300 Index |
| data source | `tushare 'daily'` dataset rows with `ts_code == 000300.SH` |
| price convention | `close` |
| adjustment convention | unadjusted close exactly as stored in the PIT snapshot |
| trading calendar | SSE/SZSE open sessions: `trade_cal` (`is_open == 1`) when supplied, otherwise the benchmark rows are the session authority |
| lookbacks | 20D and 60D (plus the 52-week-high window) |
| endpoint inclusion | as-of session `t` is the inclusive anchor; window start is the `L`-th prior open session |
| missing policy | `unknown + reason`; excess never falls back to stock-only return |
| version | `benchmark-v1` (recorded in every feature evidence) |

### Why CSI 300?

It is the only benchmark the project can currently support *honestly*:

- the forward-evaluation layer already treats `000300.SH` rows inside the
  `daily` dataset as the benchmark (`EvaluationConfig.benchmark_code`,
  `README`, `docs/scanner-evaluation.md`, evaluation tests);
- there is no separate `index_daily`/index dataset in the storage layer and no
  synced index history in `data/raw`, so CSI 500 / CSI 1000 / broad-market
  (equal-weight all-A or index-union) benchmarks are **not claimed**: their
  data infrastructure does not exist in this repository yet.

CSI 300 as `000300.SH` in `daily` is the same storage convention, the same
session authority as candidates, and requires no new download pipeline. If the
production `daily` snapshot has no `000300.SH` rows, crowding v2 is fully
`unknown` (fail-closed) until benchmark rows are synced — this is a documented
data-coverage gap, not a fallback path.

## 3. Return contract

```text
R_stock(t, L)     = P_stock(t) / P_stock(t-L) - 1
R_benchmark(t, L) = B(t) / B(t-L) - 1
excess_return(t,L)= R_stock(t, L) - R_benchmark(t, L)
```

Implemented for `L = 20` (`recent_excess_return`) and `L = 60`
(`excess_return_60d`). The window `[t-L, t]` spans `L + 1` sessions.

Component features emitted:

| Feature | Meaning |
| --- | --- |
| `recent_return_20d` | stock-only 20-session return (honest name; not excess) |
| `benchmark_return_20d` | CSI 300 20-session return |
| `recent_excess_return` | 20D excess = stock − benchmark |
| `momentum_60d` | stock-only 60-session momentum (honest name; not excess) |
| `benchmark_return_60d` | CSI 300 60-session return |
| `excess_return_60d` | 60D excess = stock − benchmark |

Every evidence entry records `formula`, `components` (stock/benchmark/excess
returns, anchor and window-start session, session count), `config` (the full
`benchmark-v1` declaration), and `semantic_version`.

## 4. Trading-session semantics

`20D / 60D / 52-week` are counts of **open trading sessions**, never calendar
days.

1. **Session axis resolution.** If a `trade_cal` frame is supplied, the axis
   is its `is_open == 1` sessions (up to and including as-of). Otherwise the
   axis is the benchmark's own valid-close sessions (the recorded market
   sessions).
2. **Anchor `t`.** The last open session on the axis at or before as-of.
   Weekends/holidays therefore roll forward to the last open session, and this
   roll is applied identically to stock and benchmark.
3. **Endpoint alignment.** The stock close and the benchmark close must both
   exist at exactly `t` and exactly `t-L`. Any mismatch is `unknown + reason`
   (see table below). Nothing is shifted quietly.
4. **Interior suspension.** A gap strictly inside the window does not
   invalidate the return: only the two endpoints are consumed. This is
   documented behavior, not a fallback.

Reason codes (all fail-closed, `unknown` status):

| Reason | Meaning |
| --- | --- |
| `no_market_history` | empty/invalid market frame |
| `benchmark_unavailable` | no benchmark rows at all |
| `benchmark_stale_at_as_of` | benchmark's last recorded session is before the stock's last quote session |
| `benchmark_missing_at_anchor_session` | calendar session exists but no benchmark close at `t` |
| `calendar_stale` | supplied trade_cal ends before the stock's quotes |
| `stock_no_market_history` | no stock rows at all |
| `stock_no_quote_at_anchor_session` | stock suspended (or truncated) at `t` |
| `insufficient_benchmark_history` | fewer than `L` prior sessions exist |
| `stock_missing_at_window_start` | no stock close at exactly `t-L` |
| `benchmark_missing_at_window_start` | no benchmark close at exactly `t-L` |
| `invalid_window_price` | zero/None endpoint price |

The one rule that never happens:

```text
benchmark unavailable -> excess return := stock return   # FORBIDDEN
```

## 5. 52-week high rule

| Parameter | Value (configurable in `BenchmarkConfig`, versioned `benchmark-v1`) |
| --- | --- |
| window | trailing 252 open sessions ending at `t` |
| as-of session | included (`high_include_as_of = True`) |
| minimum history | ≥ 60 stock close observations inside the window (`high_min_sessions`) |
| prices | unadjusted closes as stored (same convention as all returns) |
| suspension policy | missing stock closes inside the window are simply absent from the observation count; fewer than 60 → unknown |
| formula | `distance_52w_high = 1 - close(t) / max(close over window)` |

Evidence on `distance_52w_high` includes: `current_price`, `high`,
`distance`, `window_start`, `window_end`, `session_count`,
`observation_count` (also exposed as `current_price`, `high_52w`,
`high_52w_window_start`, `high_52w_window_end`, `high_52w_obs_count`).

## 6. Crowding / expectation v2 signals

| Feature (semantic version `crowding-v2`) | Formula | Default threshold | In penalty |
| --- | --- | --- | --- |
| `recent_excess_return` | 20D excess (§3) | — | via `repricing_20d` |
| `excess_return_60d` | 60D excess (§3) | — | via `repricing_60d` |
| `repricing_20d` | `min(max(excess_20d, 0) / 0.15, 1)` | 0.15 | yes |
| `repricing_60d` | `min(max(excess_60d, 0) / 0.30, 1)` | 0.30 | yes |
| `distance_52w_high` | §5 | — | via `high_proximity` |
| `high_proximity` | `min(max(1 - distance, 0), 1)` = close/high | — | yes |
| `volume_spike` | `vol(t) / median(vol over prior 60 sessions)` | 2.0 | via `volume_spike_penalty` |
| `volume_spike_penalty` | `min(max(spike - 1, 0) / (threshold - 1), 1)` | 2.0 | yes |
| `turnover_spike` | `turnover_rate(t) / median(turnover_rate over prior 60 sessions)` | 2.0 | via `turnover_spike_penalty` |
| `turnover_spike_penalty` | same shape as volume | 2.0 | yes |
| `valuation_percentile` | `P(pe <= pe(t))` over the trailing 252 sessions (field `pe_ttm`, fallback `pe`) | — | no by default |
| `valuation_penalty` | `1 - valuation_percentile` | — | opt-in only |
| `disclosure_reaction_excess` | stock − benchmark over the first 5 sessions after the latest disclosure available at as-of | 0.10 | no by default |
| `disclosure_reaction_penalty` | `min(max(excess, 0) / 0.10, 1)` | 0.10 | opt-in only |

`crowding_penalty = 100 * mean(penalty components)` and
`expectation_score = 100 - crowding_penalty`; the risk flag
`already_repriced_or_crowded` fires at `crowding_penalty >= 70`
(`crowding_flag_threshold`). Thresholds are feature-level calibration
constants recorded in every penalty's `config`; they are **not** score weights
(`scanner.score.ScoreConfig` is untouched).

### Deliberately absent or non-penalty components

- **Valuation percentile** — emitted as evidence, excluded from the penalty by
  default (`include_valuation_in_penalty=False`): `pe`/`pe_ttm` from
  `daily_basic` are raw as stored and not yet calibrated against the
  turnaround hypothesis. Missing columns → `unknown` (`valuation_unavailable`).
- **Disclosure reaction** — emitted as evidence with provable PIT timing (see
  §7), excluded from the penalty by default
  (`include_disclosure_in_penalty=False`): reaction calibration is research
  material, not a production penalty yet.

## 7. PIT boundary and disclosure reaction

All market rows are restricted to `trade_date <= as_of` and, when the frame
carries `actual_available_date`, to `actual_available_date <= as_of` — the same
rule the rest of the scanner uses.

The disclosure reaction component additionally proves:

```text
disclosure availability date (actual_date, fallback ann_date) <= as_of
```

and the reaction window consumes **only** the first `reaction_sessions` (5)
open sessions strictly **after** that availability date, ending at or before
as-of. Consequences:

- a disclosure disclosed after as-of is invisible to earlier as-of dates
  (`no_disclosure_before_as_of` if nothing is visible);
- adding a later disclosure row must not change an earlier as-of result
  (tested);
- rows with no `actual_date` and no `ann_date` can never prove timing →
  `unknown` (`disclosure_timing_unprovable`);
- the event session itself is never part of the reaction window;
- the window is benchmark-anchored, so it is aligned for stock and benchmark
  by construction.

## 8. Fundamental / Crowding structural separation

The feature groups remain structurally independent outputs:

- `features.fundamental` + `features.trend` + `features.quality` consume only
  financial frames and never see market prices;
- `features.market.compute_crowding_features` consumes only market prices,
  calendar, and disclosure frames and never sees financial statements;
- `FeatureVector.merge` concatenates namespaced values; `score_feature_vector`
  reads `fundamental_score` from purely fundamental inputs
  (`revenue_yoy`, `net_profit_yoy`, `operating_profit_yoy`, margins) and
  `expectation_score` from purely crowding inputs.

Therefore "业绩改善非常强 但过去 20D 已明显跑赢 benchmark" is reported as:

```text
Fundamental: strong     (fundamental_score high, unchanged)
Crowding: elevated      (crowding_penalty high, expectation_score low)
```

A crowding penalty is never phrased as a fundamental deterioration, and the
`already_repriced_or_crowded` risk flag belongs to the expectation group only.
This is enforced by test
`test_fundamental_and_crowding_outputs_are_independent`.

## 9. Determinism

For identical PIT inputs the output is a pure function: all joins are
deterministic, ties are resolved with stable sorts, and no randomness or
global state is used. Enforced by `test_outputs_are_deterministic` (full
`as_dict` equality).

## 10. Scope

Out of scope for `crowding-v2` (unchanged or explicitly not built):

- no momentum trading strategy, no forward-return tuning;
- no change to `ScoreConfig` weights or to the #27/#28 financial and #29
  attention semantics;
- exactly one supported benchmark identity (CSI 300 / `000300.SH`);
- no intraday/orderbook data;
- no full-history replay or large downloads in this change.

## 11. Versioning

| Artifact | Version |
| --- | --- |
| benchmark contract | `benchmark-v1` |
| crowding/expectation feature group | `crowding-v2` (vector `version` and per-feature `semantic_version`) |
| evidence schema | additive fields on `FeatureEvidence`: `semantic_version`, `formula`, `components`, `config` |
| score | unchanged `score-v1` |