"""Coarse storage capacity planning for the historical RAW foundation."""

from __future__ import annotations

import math
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

PLANNING_DATASETS: tuple[str, ...] = (
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "fina_mainbz",
    "forecast",
    "express",
    "fina_audit",
    "disclosure_date",
    "daily",
    "daily_basic",
)

# Rows per company and period are deliberately rounded planning assumptions.
# They are not canonical semantics and are replaced by observed bootstrap
# rates in the Phase 2A report.
_DATASET_ASSUMPTIONS: dict[str, tuple[int, float, bool]] = {
    "income": (4, 1.0, True),
    "balancesheet": (4, 1.0, True),
    "cashflow": (4, 1.0, True),
    "fina_indicator": (4, 1.0, True),
    "fina_mainbz": (4, 32.0, True),
    "forecast": (4, 2.0, True),
    "express": (4, 1.0, True),
    "fina_audit": (1, 1.0, True),
    "disclosure_date": (4, 1.0, False),
    "daily": (245, 1.0, False),
    "daily_basic": (245, 1.0, False),
}


@dataclass(frozen=True, slots=True)
class SampleStorageStats:
    dataset: str
    files: int
    rows: int
    size_bytes: int
    rows_per_file: float
    bytes_per_file: float
    observed_bytes_per_row: float
    compact_bytes_per_row: float
    tiny_file_warning: bool


@dataclass(frozen=True, slots=True)
class CapacityEstimate:
    dataset: str
    horizon_years: int
    expected_rows: int
    estimated_size_bytes: int
    conservative_upper_bound_bytes: int


@dataclass(frozen=True, slots=True)
class StorageCapacityPlan:
    generated_at: str
    data_dir: str
    initial_free_bytes: int
    initial_data_bytes: int
    company_count: int
    company_count_source: str
    expected_revision_multiplier: float
    upper_revision_multiplier: float
    trading_days_per_year: int
    partition_overhead_multiplier: float
    sample_stats: tuple[SampleStorageStats, ...]
    estimates: tuple[CapacityEstimate, ...]


def format_bytes(value: int | float) -> str:
    """Format bytes using binary units without losing the underlying integer."""

    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(amount) < 1024 or unit == "TiB":
            return f"{amount:,.2f} {unit}"
        amount /= 1024
    return f"{amount:,.2f} TiB"


def directory_size(path: str | Path) -> int:
    root = Path(path).expanduser()
    if not root.exists():
        return 0
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def _compact_bytes_per_row(files: list[Path], rows: int, fallback: float) -> float:
    """Estimate row payload size after removing tiny-file footer overhead."""

    if not files or rows <= 0:
        return fallback
    try:
        frame = pd.concat(
            [pd.read_parquet(path) for path in files], ignore_index=True, sort=False
        )
        if frame.empty:
            return fallback
        with tempfile.NamedTemporaryFile(suffix=".parquet") as temporary:
            table = pa.Table.from_pandas(frame, preserve_index=False)
            pq.write_table(table, temporary.name, compression="zstd")
            compact_size = Path(temporary.name).stat().st_size
        return compact_size / len(frame)
    except (OSError, ValueError, TypeError, pa.ArrowException):
        return fallback


def inspect_sample_storage(
    data_dir: str | Path,
    datasets: tuple[str, ...] = PLANNING_DATASETS,
) -> tuple[SampleStorageStats, ...]:
    """Measure existing ignored Parquet files, including tiny-file signals."""

    raw_dir = Path(data_dir).expanduser() / "raw"
    stats: list[SampleStorageStats] = []
    for dataset in datasets:
        dataset_dir = raw_dir / dataset
        files = sorted(dataset_dir.rglob("*.parquet")) if dataset_dir.exists() else []
        rows = sum(pq.ParquetFile(path).metadata.num_rows for path in files)
        size_bytes = sum(path.stat().st_size for path in files)
        observed = size_bytes / rows if rows else 0.0
        compact = _compact_bytes_per_row(files, rows, fallback=observed or 256.0)
        rows_per_file = rows / len(files) if files else 0.0
        bytes_per_file = size_bytes / len(files) if files else 0.0
        # A sample partition with only a handful of rows is not representative
        # of the eventual one-period/full-market files.
        tiny_file_warning = bool(files and (rows_per_file < 100 or bytes_per_file < 32_768))
        stats.append(
            SampleStorageStats(
                dataset=dataset,
                files=len(files),
                rows=rows,
                size_bytes=size_bytes,
                rows_per_file=rows_per_file,
                bytes_per_file=bytes_per_file,
                observed_bytes_per_row=observed,
                compact_bytes_per_row=compact,
                tiny_file_warning=tiny_file_warning,
            )
        )
    return tuple(stats)


def _base_rows(dataset: str, company_count: int, years: int, trading_days: int) -> float:
    periods_per_year, rows_per_period, _ = _DATASET_ASSUMPTIONS[dataset]
    periods = trading_days if dataset in {"daily", "daily_basic"} else periods_per_year
    return company_count * years * periods * rows_per_period


def build_capacity_plan(
    data_dir: str | Path,
    *,
    company_count: int = 5_500,
    company_count_source: str = "planning estimate",
    horizons: tuple[int, ...] = (10, 15),
    expected_revision_multiplier: float = 1.20,
    upper_revision_multiplier: float = 1.50,
    trading_days_per_year: int = 245,
    partition_overhead_multiplier: float = 1.50,
    generated_at: str | None = None,
) -> StorageCapacityPlan:
    """Build a deliberately conservative, order-of-magnitude capacity plan."""

    if company_count <= 0:
        raise ValueError("company_count must be positive")
    if not horizons or any(years <= 0 for years in horizons):
        raise ValueError("horizons must contain positive year counts")
    if expected_revision_multiplier < 1 or upper_revision_multiplier < 1:
        raise ValueError("revision multipliers must be at least one")
    if upper_revision_multiplier < expected_revision_multiplier:
        raise ValueError("upper revision multiplier must not be below expected multiplier")
    stats = inspect_sample_storage(data_dir)
    by_dataset = {stat.dataset: stat for stat in stats}
    estimates: list[CapacityEstimate] = []
    for years in horizons:
        for dataset in PLANNING_DATASETS:
            _, _, revisions_apply = _DATASET_ASSUMPTIONS[dataset]
            base = _base_rows(dataset, company_count, years, trading_days_per_year)
            expected_rows = base * (expected_revision_multiplier if revisions_apply else 1.0)
            upper_rows = base * (upper_revision_multiplier if revisions_apply else 1.25)
            bytes_per_row = by_dataset[dataset].compact_bytes_per_row or 256.0
            estimates.append(
                CapacityEstimate(
                    dataset=dataset,
                    horizon_years=years,
                    expected_rows=math.ceil(expected_rows),
                    estimated_size_bytes=math.ceil(expected_rows * bytes_per_row),
                    conservative_upper_bound_bytes=math.ceil(
                        upper_rows * bytes_per_row * partition_overhead_multiplier
                    ),
                )
            )
    data_path = Path(data_dir).expanduser()
    usage_path = data_path if data_path.exists() else data_path.parent
    initial_free_bytes = shutil.disk_usage(usage_path).free
    return StorageCapacityPlan(
        generated_at=generated_at or datetime.now(UTC).isoformat(),
        data_dir=str(data_path),
        initial_free_bytes=initial_free_bytes,
        initial_data_bytes=directory_size(data_path),
        company_count=company_count,
        company_count_source=company_count_source,
        expected_revision_multiplier=expected_revision_multiplier,
        upper_revision_multiplier=upper_revision_multiplier,
        trading_days_per_year=trading_days_per_year,
        partition_overhead_multiplier=partition_overhead_multiplier,
        sample_stats=stats,
        estimates=tuple(estimates),
    )


def render_capacity_plan_markdown(plan: StorageCapacityPlan) -> str:
    """Render a reviewable storage plan with assumptions made explicit."""

    lines = [
        "# Historical RAW storage capacity plan",
        "",
        f"- Generated at (UTC): `{plan.generated_at}`",
        f"- Data directory: `{plan.data_dir}`",
        f"- Initial free space: `{format_bytes(plan.initial_free_bytes)}`",
        f"- Initial data directory size: `{format_bytes(plan.initial_data_bytes)}`",
        f"- Current A-share company count: `{plan.company_count:,}` ({plan.company_count_source})",
        "",
        "## Planning assumptions",
        "",
        f"- Expected revision multiplier: `{plan.expected_revision_multiplier:.2f}x`",
        f"- Conservative revision multiplier: `{plan.upper_revision_multiplier:.2f}x`",
        f"- Trading days per year: `{plan.trading_days_per_year}`",
        f"- Partition/schema overhead in upper bound: `{plan.partition_overhead_multiplier:.2f}x`",
        "- Financial/report datasets use four periods per year (Q1, H1, Q3, FY).",
        "- `fina_mainbz` is planned at 32 line items per company-period; forecast at two rows.",
        "- Expected size uses compact re-encoded Zstandard row payload where possible.",
        "  Observed bytes/row from the current tiny sample is also shown and is not used",
        "  blindly because file footer overhead would materially overstate the warehouse.",
        "",
        "## Existing sample Parquet measurements",
        "",
        "| Dataset | Files | Rows | Size | Rows/file | Bytes/file | Observed bytes/row | "
        "Compact bytes/row | Tiny-file signal |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for stat in plan.sample_stats:
        lines.append(
            f"| {stat.dataset} | {stat.files} | {stat.rows:,} | {format_bytes(stat.size_bytes)} | "
            f"{stat.rows_per_file:,.1f} | {format_bytes(stat.bytes_per_file)} | "
            f"{stat.observed_bytes_per_row:,.1f} | {stat.compact_bytes_per_row:,.1f} | "
            f"{'YES' if stat.tiny_file_warning else 'NO'} |"
        )

    for years in sorted({estimate.horizon_years for estimate in plan.estimates}):
        lines.extend(
            [
                "",
                f"## {years}-year order-of-magnitude estimate",
                "",
                "| Dataset | Expected rows | Estimated Parquet | Conservative upper bound |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        estimates = [estimate for estimate in plan.estimates if estimate.horizon_years == years]
        for estimate in estimates:
            lines.append(
                f"| {estimate.dataset} | {estimate.expected_rows:,} | "
                f"{format_bytes(estimate.estimated_size_bytes)} | "
                f"{format_bytes(estimate.conservative_upper_bound_bytes)} |"
            )
        lines.append(
            f"| **TOTAL** | **{sum(item.expected_rows for item in estimates):,}** | "
            f"**{format_bytes(sum(item.estimated_size_bytes for item in estimates))}** | "
            f"**{format_bytes(sum(item.conservative_upper_bound_bytes for item in estimates))}** |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a capacity guard, not a forecast of API row availability. Revision rates,"
            " line-item counts, schema width, and current listed-company count will be replaced"
            " by actual Phase 2A bootstrap measurements. A tiny-file warning means the current"
            " sample partition layout must not be extrapolated using raw bytes/file.",
            "",
        ]
    )
    return "\n".join(lines)


def write_capacity_plan(plan: StorageCapacityPlan, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_capacity_plan_markdown(plan), encoding="utf-8")
