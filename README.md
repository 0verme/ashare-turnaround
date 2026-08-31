# ashare-turnaround

> **A-share turnaround scanner for finding low-attention companies with improving fundamentals and unpriced changes.**
>
> **A 股低关注基本面拐点扫描器：寻找那些已经开始不是原来那个它的公司。**

## What

`ashare-turnaround` is a research-oriented data foundation for A-share fundamental-turnaround analysis. The first milestone deliberately stops at a small, auditable path:

```text
Tushare-compatible API
        ↓
Thin TushareProvider
        ↓
RAW Parquet
        ↓
DuckDB read_parquet
        ↓
PIT canonical view/query
```

## Why

Fundamental research must not use information that was published after the historical decision date. This project therefore treats point-in-time availability, raw-field traceability, and a portable local-first store as more important than dataset volume.

## Current Status

Phase 0/1 and the first complete scanner path are implemented:

- one official Python `tushare` SDK boundary with an optional configurable Base URL;
- bounded API retries and classified provider errors;
- small `DatasetSpec` definitions and bounded sample pagination;
- atomic RAW Parquet partitions with `retrieved_at` and `source` provenance;
- per-dataset partition strategies (snapshot, year, or trade-date) with compact calendar storage;
- in-process DuckDB queries and a PE percentile smoke example;
- financial PIT canonical columns, bounded real revision checks, and synthetic version-chain checks;
- bounded VIP period probes with schema/PIT-risk evaluation;
- a versioned comparable-period contract for PIT-safe single-quarter,
  cumulative, YoY, QoQ, TTM, and margin primitives (unknown on ambiguity).
- raw coverage/integrity inventory with missing-partition, duplicate, schema, and
  checkpoint findings;
- Phase 1.6 Market / Reference historical corpus bootstrap: exchange-range
  calendars, explicit current reference snapshots, monthly full-market `daily` /
  `daily_basic`, `suspend_d`, and configured `000300.SH` `index_daily` history;
- idempotent date-scoped incremental synchronization for market and disclosure
  data;
- a PIT-safe investable universe, independent feature groups, transparent scoring,
  historical replay, a versioned PIT replay validation sample, provenance-complete
  forward evaluation, precommitted feature stability analysis, and provenance-first
  candidate reports.

The live source validation report is at [docs/data-source-validation.md](docs/data-source-validation.md), the VIP assessment is at [docs/vip-api-evaluation.md](docs/vip-api-evaluation.md), and the PIT evidence is at [docs/pit-field-mapping.md](docs/pit-field-mapping.md) and [docs/pit-validation.md](docs/pit-validation.md). The comparable-period contract is documented in [docs/comparable-period-semantics.md](docs/comparable-period-semantics.md). The scanner contracts and issue-to-module mapping are documented in [docs/scanner-contracts.md](docs/scanner-contracts.md). The #32 replay validation contract is documented in [docs/pit-replay-validation.md](docs/pit-replay-validation.md). Evaluation assumptions are frozen in [docs/scanner-evaluation.md](docs/scanner-evaluation.md), and the ablation decision rule is in [docs/feature-ablation.md](docs/feature-ablation.md). Phase 1.6 decisions and final gates are documented in [docs/market-reference-history.md](docs/market-reference-history.md) and [docs/market-reference-coverage.md](docs/market-reference-coverage.md). Full-market historical bootstrap is an explicit, resumable operation; tests use synthetic fixtures and local Parquet only.

## Phase 2.5 hand-off

Issue #34 is in close-out state: the 2012–2025 Market / Reference corpus is
verified locally, while Financial P0 remains untouched. Historical data work
now follows **GAP-DRIVEN MODE**: only a correctness/replay gate failure or an
approved incremental-sync need may request a bounded repair. No full-history
redownload is implied by this hand-off.

The #32 validation path is read-only and must remain separate from Evaluation
and Ablation. The planned post-calibration sequence is `#32 → calibrated
Evaluation (#17) → Ablation / Stability (#18) → Score v2 decision`. The
post-#41 normalized artifact audit is in
[docs/pit-replay-artifact-normalization.md](docs/pit-replay-artifact-normalization.md);
the resource/cutoff contract and final v3 full validation pair are in
[docs/pit-replay-resource-gate-v3.md](docs/pit-replay-resource-gate-v3.md) (the
v2 run is preserved as historical evidence). The execution-only finalization
repair and its detailed evidence are in
[docs/pit-replay-finalization-working-set.md](docs/pit-replay-finalization-working-set.md).

## Architecture

- `src/ashare_turnaround/providers/tushare.py` — the only Tushare client construction and transport override.
- `src/ashare_turnaround/datasets/` — dataset contracts, bounded sample synchronization,
  Market / Reference historical bootstrap, and coverage verification.
- `src/ashare_turnaround/storage/` — local Parquet, JSON sync state, and capacity guards.
- `src/ashare_turnaround/query/` — in-process DuckDB access.
- `src/ashare_turnaround/pit/` — financial normalization and as-of selection.
- `src/ashare_turnaround/features/` — independent fundamental, trend, quality,
  attention, and crowding feature groups. Expectation/crowding v2 is
  benchmark-relative (CSI 300 / `000300.SH` from `index_basic` + `index_daily`);
  see
  `docs/expectation-crowding-v2.md` for the frozen contract.
- `src/ashare_turnaround/scanner/` — universe, score, evidence-confidence gate,
  replay, daily snapshot, evaluation, ablation, and explainable report workflows.
  The additive gate is documented in `docs/evidence-confidence-v1.md`.
- `data/` — local runtime data; raw/derived/state/reports are ignored by Git.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m compileall -q src tests
```

The minimal CLI is:

```bash
.venv/bin/python -m ashare_turnaround preflight
.venv/bin/python -m ashare_turnaround validate-source
.venv/bin/python -m ashare_turnaround validate-source --vip
.venv/bin/python -m ashare_turnaround sync-sample --dry-run
.venv/bin/python -m ashare_turnaround sync-sample
.venv/bin/python -m ashare_turnaround pit-check
.venv/bin/python -m ashare_turnaround market-capacity-plan --start-date 20120101 --end-date 20251231
.venv/bin/python -m ashare_turnaround bootstrap-market --dry-run --start-date 20120101 --end-date 20251231
.venv/bin/python -m ashare_turnaround verify-market --start-date 20120101 --end-date 20251231
.venv/bin/python -m ashare_turnaround inventory --as-of 20250630
.venv/bin/python -m ashare_turnaround sync-daily --date 20250630
.venv/bin/python -m ashare_turnaround replay --as-of 20250630 --top 20
.venv/bin/python -m ashare_turnaround replay-variants --as-of 20250630 --top 20
.venv/bin/python -m ashare_turnaround replay-validate --stage smoke --start 2017-01 --end 2026-12 --today 20260830
.venv/bin/python -m ashare_turnaround replay-profile --as-of 20250616 --candidate-cap 100
.venv/bin/python -m ashare_turnaround artifact-audit --input data/reports/replay-validation/snapshots/<snapshot>.json
.venv/bin/python -m ashare_turnaround scan --top 20
.venv/bin/python -m ashare_turnaround evaluate --scans data/derived/scans/scan-20250630.parquet --benchmark-code 000300.SH --fundamentals data/derived/research/fundamental-history.parquet
.venv/bin/python -m ashare_turnaround ablate fundamental_only=data/reports/evaluation-fundamental_only.json quality_added=data/reports/evaluation-quality_added.json attention_added=data/reports/evaluation-attention_added.json expectation_added=data/reports/evaluation-expectation_added.json
.venv/bin/python -m ashare_turnaround report --as-of 20250630
```

`sync-sample --dry-run` only renders the bounded request plan. It does not
construct a provider, contact the remote endpoint, create data directories, or
change Parquet/state files. Paginated reads fail closed when a page bound is
exhausted or a duplicate page is observed; a failed sample request never
replaces an existing dataset partition.

`sync-daily` records every dataset outcome as `success`, `not_due`, `pending`,
`partial`, or `failed`. Empty or pagination-incomplete responses are not treated
as complete data. Its stock reference refresh includes listed, delisted, and
pre-listing statuses for dated-universe reconstruction. The Phase 1.6
`bootstrap-market` command uses month/range units rather than a ten-year
`sync-daily` loop; `verify-market` performs coverage and integrity checks only.
`replay` and `scan`
accept an explicit `--as-of` for historical reproduction; `artifact-audit`
reads an existing JSON artifact without touching RAW and reports recursive
size attribution plus legacy/normalized size; omitting `--as-of` from `scan` selects
the latest open date in the local trade calendar. Evaluation
aligns candidates and benchmarks to the same future market dates and preserves
failed, delisted, exposure, and PIT-fundamental evidence in its report.

## Data Source

Production data access uses the official Tushare Python SDK by default.

An optional `TUSHARE_BASE_URL` configuration is available for
Tushare-compatible endpoints. Leave it unset to use the SDK default endpoint.

```env
TUSHARE_TOKEN=
TUSHARE_BASE_URL=
ASHARE_DATA_DIR=./data
ASHARE_BENCHMARK_CODE=000300.SH
```

Copy `.env.example` to `.env` for local development.
`.env` and runtime data are ignored by Git and must not be committed.
Tokens and private endpoint configuration are never written to reports, logs,
fixtures, or Parquet business columns.

## Disclaimer

This project is for data research and technical experiments only. It is not investment advice, does not promise returns, does not recommend securities, and does not provide automated trading.

## License

MIT. See [LICENSE](LICENSE).
