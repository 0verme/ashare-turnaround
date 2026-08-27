"""Capacity estimates for the Market / Reference historical corpus."""

from __future__ import annotations

import math
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..datasets.market_bootstrap import (
    DEFAULT_BENCHMARK_CODE,
    DEFAULT_MARKET_EXCHANGES,
    _normalized_date,
)
from ..storage.parquet import RawParquetStore

MARKET_CAPACITY_DATASETS: tuple[str, ...] = (
    "trade_cal",
    "stock_basic",
    "index_basic",
    "namechange",
    "suspend_d",
    "daily",
    "daily_basic",
    "index_daily",
)
_DEFAULT_BYTES_PER_ROW = {
    "trade_cal": 64.0,
    "stock_basic": 320.0,
    "index_basic": 220.0,
    "namechange": 180.0,
    "suspend_d": 90.0,
    "daily": 72.0,
    "daily_basic": 125.0,
    "index_daily": 72.0,
}


@dataclass(frozen=True, slots=True)
class MarketCapacityEstimate:
    dataset: str
    estimated_rows: int
    expected_size_bytes: int
    conservative_size_bytes: int
    bytes_per_row: float
    basis: str


@dataclass(frozen=True, slots=True)
class MarketCapacityPlan:
    generated_at: str
    data_dir: str
    start_date: str
    end_date: str
    benchmark_code: str
    exchanges: tuple[str, ...]
    trading_days_estimate: int
    company_count: int
    company_count_source: str
    sample_basis: str
    initial_free_bytes: int
    expected_total_bytes: int
    conservative_total_bytes: int
    safety_margin_bytes: int
    estimates: tuple[MarketCapacityEstimate, ...]
    status: str

    @property
    def safe_to_download(self) -> bool:
        return self.status == "PASS"


def _compact_bytes_per_row(frame: pd.DataFrame | None, fallback: float) -> float:
    if frame is None or frame.empty:
        return fallback
    try:
        with tempfile.NamedTemporaryFile(suffix=".parquet") as temporary:
            table = pa.Table.from_pandas(frame.reset_index(drop=True), preserve_index=False)
            pq.write_table(table, temporary.name, compression="zstd")
            size = Path(temporary.name).stat().st_size
        return max(1.0, size / len(frame))
    except (OSError, ValueError, TypeError, pa.ArrowException):
        return fallback


def _open_days(start: str, end: str) -> int:
    # Calendar data is preferred when already present.  The capacity estimate
    # must also work before Stage 1, so weekday sessions provide a conservative
    # bounded fallback (holiday reduction is applied below).
    days = pd.bdate_range(start, end)
    return max(1, math.ceil(len(days) * 245 / 261))


def _sample_frames(data_dir: Path, datasets: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    store = RawParquetStore(data_dir)
    frames: dict[str, pd.DataFrame] = {}
    for dataset in datasets:
        files = store.parquet_files(dataset)
        if not files:
            continue
        # Only use small pre-existing samples as row-width observations.  A
        # large historical dataset should be measured by metadata, not loaded
        # into pandas merely to build a capacity report.
        rows = sum(int(pq.ParquetFile(path).metadata.num_rows) for path in files)
        if rows <= 100_000:
            try:
                frames[dataset] = store.read(dataset)
            except (OSError, ValueError, RuntimeError):
                continue
    return frames


def _observed_rows_per_session(frame: pd.DataFrame | None) -> float | None:
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["trade_date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return len(frame) / dates.dt.normalize().nunique()


def build_market_capacity_plan(
    data_dir: str | Path,
    *,
    start_date: str = "20120101",
    end_date: str | None = None,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    exchanges: tuple[str, ...] = DEFAULT_MARKET_EXCHANGES,
    company_count: int | None = None,
    company_count_source: str | None = None,
    sample_frames: Mapping[str, pd.DataFrame] | None = None,
    generated_at: str | None = None,
) -> MarketCapacityPlan:
    """Estimate rows and compressed size before a full remote download.

    The estimate intentionally combines a bounded/local sample row width with
    a market-growth factor.  It is a guardrail, not an assertion about API
    availability or an investment result.
    """

    data_path = Path(data_dir).expanduser()
    start = _normalized_date(start_date, name="start_date")
    end = _normalized_date(end_date or f"{datetime.now(UTC).year - 1:04d}1231", name="end_date")
    if end < start:
        raise ValueError("end_date must not be earlier than start_date")
    if company_count is not None and company_count <= 0:
        raise ValueError("company_count must be positive")
    store = RawParquetStore(data_path)
    local_reference_count = 0
    try:
        reference = store.read("stock_basic")
        if "ts_code" in reference.columns:
            local_reference_count = int(reference["ts_code"].dropna().astype(str).nunique())
    except (OSError, ValueError, RuntimeError):
        reference = pd.DataFrame()
    if company_count is None:
        # Existing sample references are deliberately not extrapolated.  A
        # bounded full-market stock_basic call can replace this fallback before
        # the operator approves the historical download.
        company_count = max(5500, local_reference_count)
        source = company_count_source or (
            "conservative fallback (no full reference snapshot)"
            if local_reference_count < 1000
            else "local stock_basic distinct ts_code"
        )
    else:
        source = company_count_source or "operator supplied"
    frames = dict(sample_frames or _sample_frames(data_path, MARKET_CAPACITY_DATASETS))
    trading_days = _open_days(start, end)
    sample_daily_rows = _observed_rows_per_session(frames.get("daily"))
    sample_basic_rows = _observed_rows_per_session(frames.get("daily_basic"))
    sample_basis = "bounded/existing sample row width"
    if sample_daily_rows is None or sample_daily_rows < 100:
        sample_basis += "; daily sample too small for row-rate extrapolation"
    # Average listed population over the 2012–2025 horizon is lower than the
    # current endpoint snapshot.  Conservative size assumes the current count
    # throughout and adds a 25% schema/revision/partition allowance.
    growth_average = 0.74
    rows_by_dataset: dict[str, tuple[int, int, str]] = {
        "trade_cal": (
            math.ceil((pd.Timestamp(end) - pd.Timestamp(start)).days + 1) * len(exchanges),
            1,
            "calendar days x exchanges",
        ),
        "stock_basic": (company_count, 1, "current L/D/P reference snapshot"),
        "index_basic": (1, 1, "one configured benchmark definition"),
        "namechange": (company_count * 8, 1, "bounded historical name-change allowance"),
        "suspend_d": (company_count * 35, 1, "bounded historical suspension allowance"),
        "daily": (
            math.ceil(trading_days * company_count * growth_average)
            if sample_daily_rows is None or sample_daily_rows < 100
            else math.ceil(trading_days * sample_daily_rows),
            1,
            "market-growth adjusted sessions",
        ),
        "daily_basic": (
            math.ceil(trading_days * company_count * growth_average)
            if sample_basic_rows is None or sample_basic_rows < 100
            else math.ceil(trading_days * sample_basic_rows),
            1,
            "market-growth adjusted sessions",
        ),
        "index_daily": (trading_days, 1, "one configured benchmark per session"),
    }
    estimates: list[MarketCapacityEstimate] = []
    for dataset in MARKET_CAPACITY_DATASETS:
        rows, _, basis = rows_by_dataset[dataset]
        bytes_per_row = _compact_bytes_per_row(frames.get(dataset), _DEFAULT_BYTES_PER_ROW[dataset])
        expected = math.ceil(rows * bytes_per_row)
        multiplier = 1.25 if dataset in {"daily", "daily_basic", "index_daily"} else 1.50
        conservative = math.ceil(expected * multiplier)
        estimates.append(
            MarketCapacityEstimate(
                dataset=dataset,
                estimated_rows=rows,
                expected_size_bytes=expected,
                conservative_size_bytes=conservative,
                bytes_per_row=bytes_per_row,
                basis=basis,
            )
        )
    usage_path = data_path if data_path.exists() else data_path.parent
    free = shutil.disk_usage(usage_path).free
    expected_total = sum(value.expected_size_bytes for value in estimates)
    conservative_total = sum(value.conservative_size_bytes for value in estimates)
    margin = free - conservative_total
    status = "PASS" if margin >= 15 * 1024**3 else "STOP"
    return MarketCapacityPlan(
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        data_dir=str(data_path),
        start_date=start,
        end_date=end,
        benchmark_code=str(benchmark_code).upper(),
        exchanges=tuple(exchanges),
        trading_days_estimate=trading_days,
        company_count=company_count,
        company_count_source=source,
        sample_basis=sample_basis,
        initial_free_bytes=free,
        expected_total_bytes=expected_total,
        conservative_total_bytes=conservative_total,
        safety_margin_bytes=margin,
        estimates=tuple(estimates),
        status=status,
    )


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:,.2f} {unit}"
        amount /= 1024
    return f"{amount:,.2f} TiB"


def market_capacity_dict(plan: MarketCapacityPlan) -> dict[str, object]:
    return asdict(plan)


def render_market_capacity_plan(plan: MarketCapacityPlan) -> str:
    lines = [
        "# Market / Reference historical capacity plan",
        "",
        f"- Generated at (UTC): `{plan.generated_at}`",
        f"- Data directory: `{plan.data_dir}`",
        f"- Research window: `{plan.start_date}..{plan.end_date}`",
        f"- Benchmark: `{plan.benchmark_code}`",
        f"- Exchanges: `{', '.join(plan.exchanges)}`",
        f"- Estimated trading sessions: `{plan.trading_days_estimate:,}`",
        f"- Company count: `{plan.company_count:,}` ({plan.company_count_source})",
        f"- Sample basis: `{plan.sample_basis}`",
        f"- Initial free space: `{_format_bytes(plan.initial_free_bytes)}`",
        f"- Expected total: `{_format_bytes(plan.expected_total_bytes)}`",
        f"- Conservative total: `{_format_bytes(plan.conservative_total_bytes)}`",
        f"- Conservative safety margin: `{_format_bytes(plan.safety_margin_bytes)}`",
        f"- Capacity gate: **`{plan.status}`**",
        "",
        "| Dataset | Estimated rows | Expected size | Conservative | Bytes/row | Basis |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for value in plan.estimates:
        lines.append(
            f"| {value.dataset} | {value.estimated_rows:,} | "
            f"{_format_bytes(value.expected_size_bytes)} | "
            f"{_format_bytes(value.conservative_size_bytes)} | "
            f"{value.bytes_per_row:.1f} | {value.basis} |"
        )
    lines.extend(
        [
            "",
            (
                "The estimate is a preflight guard.  It uses compact Parquet row-width "
                "measurements where a bounded/local sample is available, a market-growth "
                "adjustment for historical company counts, and conservative partition/"
                "schema allowances.  It does not contact the remote provider and it does "
                "not rewrite Financial P0 data."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_market_capacity_plan(plan: MarketCapacityPlan, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_market_capacity_plan(plan), encoding="utf-8")
