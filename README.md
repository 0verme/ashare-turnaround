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
- a cumulative income/cash-flow single-quarter prototype.
- raw coverage/integrity inventory with missing-partition, duplicate, schema, and
  checkpoint findings;
- idempotent date-scoped incremental synchronization for market and disclosure
  data;
- a PIT-safe investable universe, independent feature groups, transparent scoring,
  historical replay, provenance-complete forward evaluation, precommitted feature
  stability analysis, and provenance-first candidate reports.

The live source validation report is at [docs/data-source-validation.md](docs/data-source-validation.md), the VIP assessment is at [docs/vip-api-evaluation.md](docs/vip-api-evaluation.md), and the PIT evidence is at [docs/pit-field-mapping.md](docs/pit-field-mapping.md) and [docs/pit-validation.md](docs/pit-validation.md). The scanner contracts and issue-to-module mapping are documented in [docs/scanner-contracts.md](docs/scanner-contracts.md). Evaluation assumptions are frozen in [docs/scanner-evaluation.md](docs/scanner-evaluation.md), and the ablation decision rule is in [docs/feature-ablation.md](docs/feature-ablation.md). Full-market historical bootstrap remains an explicit, resumable operation; tests use synthetic fixtures and local Parquet only.

## Architecture

- `src/ashare_turnaround/providers/tushare.py` — the only Tushare client construction and transport override.
- `src/ashare_turnaround/datasets/` — dataset contracts and bounded sample synchronization.
- `src/ashare_turnaround/storage/` — local Parquet and JSON sync state.
- `src/ashare_turnaround/query/` — in-process DuckDB access.
- `src/ashare_turnaround/pit/` — financial normalization and as-of selection.
- `src/ashare_turnaround/features/` — independent fundamental, trend, quality,
  attention, and crowding feature groups.
- `src/ashare_turnaround/scanner/` — universe, score, replay, daily snapshot,
  evaluation, ablation, and explainable report workflows.
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
.venv/bin/python -m ashare_turnaround inventory --as-of 20250630
.venv/bin/python -m ashare_turnaround sync-daily --date 20250630
.venv/bin/python -m ashare_turnaround replay --as-of 20250630 --top 20
.venv/bin/python -m ashare_turnaround replay-variants --as-of 20250630 --top 20
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
pre-listing statuses for dated-universe reconstruction. `replay` and `scan`
accept an explicit `--as-of` for historical reproduction; omitting it from
`scan` selects the latest open date in the local trade calendar. Evaluation
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
```

Copy `.env.example` to `.env` for local development.
`.env` and runtime data are ignored by Git and must not be committed.
Tokens and private endpoint configuration are never written to reports, logs,
fixtures, or Parquet business columns.

## Disclaimer

This project is for data research and technical experiments only. It is not investment advice, does not promise returns, does not recommend securities, and does not provide automated trading.

## License

MIT. See [LICENSE](LICENSE).
