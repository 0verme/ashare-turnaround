# Phase 1.6 — Market / Reference Historical Foundation

## Scope and boundary

This phase builds the Market / Reference corpus only.  The declared research
window is `20120101..20251231`: the start follows the Financial P0 contract and
the end is the latest conservative complete annual boundary at execution time
(the project clock is 2026, so the previous complete year is 2025).  The end
can be overridden explicitly; it is never inferred from a current snapshot.

Financial P0 (`income`, `balancesheet`, `cashflow`, `fina_indicator`) was not
redownloaded, rewritten, compacted, or added to the Market checkpoint file.

## Issue

- GitHub issue: [#34](https://github.com/0verme/ashare-turnaround/issues/34)
- Title: `feat: build market and reference historical research corpus`
- Labels: `priority:P0`, `type:data`, `phase:1-foundation`
- Status: open

The issue records the PIT, coverage, capacity, dry-run, integrity, and
cross-sectional acceptance criteria for #29, #30, #31, #32, #17, and #18.

## Architecture audit and decision

### Reused components

- `TushareProvider` is still the sole SDK/transport boundary.
- `fetch_paginated_audited` is reused for offset pagination, duplicate-page
audit, schema audit, bounded page limits, and fail-closed partial responses.
- `RateLimiter` is shared by all workers and retry attempts.
- `RawParquetStore` remains the atomic Zstandard Parquet writer.
- `check_disk_space` is checked before submission and before the CLI starts a
real run.
- Existing `Settings`, provenance fields, and date normalization are reused.

### New components

- `datasets.market_bootstrap.bootstrap_market_data`: Market / Reference
historical orchestrator; it does not call `bootstrap_datasets` and does not use
`sync-daily` as a ten-year loop.
- `MarketCheckpointStore` and `market-bootstrap-checkpoints.json`: a separate
checkpoint namespace, so Financial P0 resume/completeness semantics are
unchanged.
- `MarketBootstrapRunLock`: prevents two Market backfills from writing the
same data/checkpoint at once.
- `datasets.market_validation.verify_market_corpus`: metadata/DuckDB coverage,
cross-section, benchmark, forward-window, symbol-sample, and integrity gates.
- `storage.market_planning.build_market_capacity_plan`: no-network capacity
gate using bounded/local row-width measurements and market-growth assumptions.
- `RawParquetStore.write_unit`: atomic named-unit storage for month, range, and
snapshot partitions.

### Partition strategy

| Dataset | Durable unit | RAW layout |
| --- | --- | --- |
| `trade_cal` | exchange + complete range | `exchange=SSE/range=20120101-20251231/` and `SZSE/...` |
| `stock_basic` | one current reference snapshot assembled from L/D/P | `snapshot=20260827/` |
| `index_basic` | one configured benchmark definition snapshot | `snapshot=20260827/` |
| `suspend_d` | complete historical date range | `range=20120101-20251231/` |
| `daily` | calendar month | `year=YYYY/month=YYYYMM/` |
| `daily_basic` | calendar month | `year=YYYY/month=YYYYMM/` |
| `index_daily` | configured benchmark + calendar month | `ts_code=000300.SH/year=YYYY/month=YYYYMM/` |

The two 168-month stock datasets therefore have 168 durable files each, not
approximately 3,400 one-day files.  The benchmark also uses monthly units;
its one-row-per-session partitions are not treated as abnormal tiny files.
`sync-daily` remains the date-scoped incremental contract.

### Operational controls

The production runs used bounded workers (`2` for the reference range run,
`4` for stock daily and benchmark history), one global `60 requests/minute`
limiter, bounded retries, atomic writes, coordinator-only commits, separate
failure isolation, and a `100` page/unit bound.  A unit is `PASS` only after a
terminal paginated response, quality checks, atomic Parquet write, and a
checkpoint append.  A partial/duplicate/schema-failed unit is retried rather
than labeled complete.

## Capacity and dry-run

A bounded full-market January 2025 probe (not stored as RAW) returned:

- `daily`: 96,779 rows, 18 sessions, 5,395 symbols, 20 pages;
- `daily_basic`: 96,779 rows, 18 sessions, 5,395 symbols, 20 pages.

The preflight capacity report is [market-capacity-plan.md](market-capacity-plan.md):

| Dataset | Estimated rows | Expected size | Conservative |
| --- | ---: | ---: | ---: |
| `daily` | 18,441,777 | 618.63 MiB | 773.29 MiB |
| `daily_basic` | 18,441,777 | 1.25 GiB | 1.57 GiB |
| `index_daily` | 3,430 | 241.17 KiB | 301.46 KiB |
| all Market/Reference datasets | — | 1.88 GiB | 2.36 GiB |

The measured free-space baseline was `372.52 GiB`; conservative margin was
`370.16 GiB`, so the gate was `PASS`.  The final dry-run against the shared
runtime data directory reported:

- planned core units: `509`;
- existing completed units: `509`;
- remaining units: `0`;
- remaining request estimate: `0`;
- workers: `4`;
- global rate limit: `60 requests/minute`;
- `remote_requests=false`, `parquet_writes=false`, `state_changes=false`.

Command:

```bash
ASHARE_DATA_DIR=/vol5/1000/ai-workspace/repos/ashare-turnaround/data \
  python -m ashare_turnaround bootstrap-market --dry-run \
  --start-date 20120101 --end-date 20251231 --benchmark-code 000300.SH
```

## Download result

Final latest-unit state in the shared data directory:

| Dataset | Requested units | PASS units | Failed/partial latest units | Rows | RAW size |
| --- | ---: | ---: | ---: | ---: | ---: |
| `trade_cal` | 2 | 2 | 0 | 10,228 | 58.25 KiB |
| `stock_basic` | 1 | 1 | 0 | 5,894 | 220.01 KiB |
| `index_basic` | 1 | 1 | 0 | 1 | 8.25 KiB |
| `suspend_d` | 1 | 1 | 0 | 428,700 | 318.06 KiB |
| `daily` | 168 | 168 | 0 | 12,498,589 | 406.02 MiB |
| `daily_basic` | 168 | 168 | 0 | 12,407,491 | 855.72 MiB |
| `index_daily` | 168 | 168 | 0 | 3,400 | 1.65 MiB |

The first `daily_basic` process was stopped by the execution timeout after
161 units had committed; resume completed the remaining units.  A transient
source duplicate was observed for `2015-09`, remained `PARTIAL`, and passed on
the explicit resume.  No partial unit was promoted to `PASS`.

## Coverage verification

The generated machine report is stored at
`data/state/market-coverage.json` in the shared runtime directory and the
human-readable report is [market-reference-coverage.md](market-reference-coverage.md).

- `trade_cal`: SSE and SZSE complete for 5,114 calendar dates each; no missing
calendar dates.
- `daily`: 3,400 expected SSE sessions, 3,400 present; actual range
`20120104..20251231`.
- `daily_basic`: 3,400 expected, 3,400 present; actual range
`20120104..20251231`.
- `index_daily`: 3,400 expected benchmark sessions, 3,400 present; actual range
`20120104..20251231`.
- all target Parquet files are readable, with no zero-byte, temporary, schema,
duplicate, or checkpoint/file mismatch findings after the pre-existing sample
quarantine described below.
- Schema audit confirms `daily.amount` is retained from the daily endpoint;
`daily_basic` uses its actual turnover/market-cap/valuation fields and does not
fabricate a second `amount` field.

### Cross-section gate

| Trade date | `daily` symbols | `daily_basic` symbols | Join | Join coverage |
| --- | ---: | ---: | ---: | ---: |
| 20130104 | 2,406 | 2,406 | 2,406 | 1.0000 |
| 20160104 | 2,592 | 2,549 | 2,549 | 0.9834 |
| 20180102 | 3,282 | 3,252 | 3,252 | 0.9909 |
| 20200102 | 3,797 | 3,741 | 3,741 | 0.9853 |
| 20220104 | 4,737 | 4,670 | 4,670 | 0.9859 |
| 20240102 | 5,329 | 5,329 | 5,329 | 1.0000 |
| 20250102 | 5,369 | 5,369 | 5,369 | 1.0000 |

Every sampled date is a broad cross-section, not a four-code sample.  The
small daily/daily_basic differences are exposed as `daily_only`; they are not
silently filled.

### Dynamic symbol history

The verifier selected symbols from the downloaded data, rather than using a
fixed hard-coded list.  The sample includes old listings, recent listings,
SSE/SZSE/BSE symbols, and multiple board categories where available.  It
records `list_date`, exchange/market snapshot values, and independent daily /
daily_basic earliest/latest dates and row counts in the machine report.

### Forward windows

The 3,400-session daily calendar supports 20D, 60D, 120D, and 250D forward
windows.  The final 20/60/120/250 sessions are explicitly reported as
right-censored tails; they are not classified as missing data.

## Benchmark

- Main benchmark: `000300.SH` (CSI 300).
- Definition endpoint: `index_basic`; daily endpoint: `index_daily`.
- Price convention: source `close`, unadjusted index level; no alternative
benchmark was selected using returns.
- Coverage: `20120104..20251231`, `3,400/3,400` sessions, `0` missing.
- Dynamic 20D sample on `20251231`: stock `000001.SZ` return
`-0.0121212121`, benchmark return `0.0218251687`, excess difference
`-0.0339463808`.

The benchmark identity and convention are explicit in the bootstrap plan and
are not hidden in a feature formula.  `index_daily` is kept separate from
stock `daily`; a future evaluator must load the declared benchmark series and
must not silently fall back to an absolute stock return.

## Reference PIT findings

### PIT-safe / conditionally safe

- `ts_code`/`symbol`: stable identifier, not a historical status observation.
- `list_date`: use only for `list_date <= as_of`.
- `delist_date`: use only when the source supplies a non-null date and the
boundary rule is declared.
- `suspend_d.trade_date` / `suspend_type`: dated suspension observation; no
separate publication timestamp is exposed, so its exact as-of convention must
be declared by the downstream universe layer.
- `namechange` fields would be usable only with an announcement-date cutoff,
if a stable source identity can be established.

### Current-snapshot-only

`stock_basic` was explicitly queried as L/D/P status snapshots with
`exchange`, `list_date`, `delist_date`, `list_status`, `market`, `industry`,
`is_hs`, and related fields.  The snapshot is useful for identifiers and
listing/delisting boundaries, but `name`, `list_status`, `industry`, `market`,
`exchange` classification, `is_hs`, and actual-control fields must not be
projected backward into 2012–2025.  In the completion matrix this is
`UNSUPPORTED_PIT`, not a fabricated historical state.

### Unsupported / partial

The pre-download `namechange` probe showed 7,640 rows over the requested mode,
including 6,043 repeated identical visible identities.  The endpoint did not
expose a stable identity/version field that would distinguish those rows.
The downloader therefore preserved the fail-closed behavior: the response was
recorded as `PARTIAL` and was not materialized as a `PASS` RAW unit.  Historical
ST/name state remains `UNKNOWN/UNSUPPORTED_PIT`; the current stock snapshot is
never used as a substitute.  `namechange` remains an explicit opt-in dataset
for a future source/identity resolution, not an unbounded per-security loop.

## RAW integrity and legacy sample handling

The pre-existing Phase 1 sample files overlapped the new full corpus and had
smaller schemas.  They were **quarantined, not deleted**, under:

```text
data/archive/phase1-samples/raw/
```

Specifically, the old root `stock_basic`, old 2024 `trade_cal` year file, and
2026 date-scoped daily/daily_basic sample partitions were moved out of
`data/raw`.  This avoids silently reading duplicated sample identities in the
historical corpus while preserving the original files for audit.  The action
was limited to Market/sample data; Financial P0 files were not touched.

Final target RAW integrity:

```text
unreadable: 0
zero byte: 0
tmp/partial: 0
schema drift: 0
duplicate primary-key rows: 0
checkpoint/file mismatch: 0
unexpected tiny stock partitions: 0
```

## Scanner safety

No `scan`, `replay`, `evaluate`, `ablate`, or candidate report was run during
this phase.  No feature, score, or scanner module was changed.  The only
runtime artifacts produced are the Market checkpoints, capacity/coverage
reports, and the Market/Reference RAW corpus.

## Remaining gaps

1. Historical ST/name/industry/board state is not fully PIT-safe.  `stock_basic`
current fields are explicitly snapshot-only.
2. `namechange` needs a source response with a stable exposed source identity
or a separately validated identity/version contract before it can be used as a
historical state table.
3. `suspend_d` is stored and complete, but the existing `UniverseConfig`/
scanner path has not been changed in this data-foundation phase to consume it;
that integration belongs to a later explicitly scoped universe/replay change.
4. `index_daily` is ready as a separate benchmark series.  #30 must explicitly
join it for excess-return features; this phase does not modify #30 strategy
logic.

## Verdict

**MARKET / REFERENCE HISTORICAL CORPUS READY**

This verdict means the critical trade calendar, full-market stock daily and
daily_basic history, configured benchmark history, and reference foundation
pass the data/coverage/integrity gates.  It does **not** upgrade the listed
current-snapshot reference fields into historical PIT facts; those limitations
remain machine-readable and documented above.
