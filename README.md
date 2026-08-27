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

Phase 0 and the Phase 1 foundation are implemented:

- one official Python `tushare` SDK boundary with an optional configurable Base URL;
- bounded API retries and classified provider errors;
- small `DatasetSpec` definitions and bounded sample pagination;
- atomic RAW Parquet partitions with `retrieved_at` and `source` provenance;
- per-dataset partition strategies (snapshot, year, or trade-date) with compact calendar storage;
- in-process DuckDB queries and a PE percentile smoke example;
- financial PIT canonical columns, bounded real revision checks, and synthetic version-chain checks;
- bounded VIP period probes with schema/PIT-risk evaluation;
- a cumulative income/cash-flow single-quarter prototype.

The live source validation report is at [docs/data-source-validation.md](docs/data-source-validation.md), the VIP assessment is at [docs/vip-api-evaluation.md](docs/vip-api-evaluation.md), and the PIT evidence is at [docs/pit-field-mapping.md](docs/pit-field-mapping.md) and [docs/pit-validation.md](docs/pit-validation.md). No full-market historical bootstrap, factors, scanner, dashboard, or trading code is included in this phase.

## Architecture

- `src/ashare_turnaround/providers/tushare.py` — the only Tushare client construction and transport override.
- `src/ashare_turnaround/datasets/` — dataset contracts and bounded sample synchronization.
- `src/ashare_turnaround/storage/` — local Parquet and JSON sync state.
- `src/ashare_turnaround/query/` — in-process DuckDB access.
- `src/ashare_turnaround/pit/` — financial normalization and as-of selection.
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
.venv/bin/python -m ashare_turnaround sync-sample
.venv/bin/python -m ashare_turnaround pit-check
```

## Data Source

Production data calls use the official Tushare Python SDK. The SDK client can receive an optional `TUSHARE_BASE_URL` override, so switching to the official endpoint is an environment-only change:

```env
TUSHARE_TOKEN=
TUSHARE_BASE_URL=https://t.xiaodefa.top/
ASHARE_DATA_DIR=./data
```

Copy this to `.env` locally; `.env` and runtime data are ignored. Tokens are never put in reports, logs, fixtures, or Parquet business columns. MCP and a seller-specific HTTP API are not part of the production data chain.

## Disclaimer

This project is for data research and technical experiments only. It is not investment advice, does not promise returns, does not recommend securities, and does not provide automated trading.

## License

MIT. See [LICENSE](LICENSE).
