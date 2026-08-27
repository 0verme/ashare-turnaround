"""Tushare-compatible API validation matrix and report rendering."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .datasets.specs import API_VALIDATION_ORDER, CORE_DATASETS, VIP_API_NAMES, get_dataset_spec
from .pit.financial import PIT_MAPPINGS
from .providers.tushare import ProviderError, TushareProvider


@dataclass(frozen=True, slots=True)
class ApiValidationResult:
    api: str
    status: str
    rows: int
    duration_seconds: float
    fields: tuple[str, ...] = ()
    notes: str = ""
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationReport:
    results: tuple[ApiValidationResult, ...]
    sample_code: str
    generated_at: str
    token_configured: bool
    vip_requested: bool = False

    @property
    def core_failures(self) -> tuple[ApiValidationResult, ...]:
        return tuple(
            result
            for result in self.results
            if result.api in CORE_DATASETS and result.status in {"FAIL", "SCHEMA_MISMATCH"}
        )


def _params_for(api: str, sample_code: str) -> dict[str, Any]:
    if api == "stock_basic":
        return {"exchange": "SSE", "list_status": "L", "limit": 3}
    if api == "trade_cal":
        return {
            "exchange": "SSE",
            "start_date": "20240101",
            "end_date": "20240131",
            "limit": 3,
        }
    if api in {"daily", "daily_basic"}:
        return {
            "ts_code": sample_code,
            "start_date": "20240101",
            "end_date": "20240131",
            "limit": 3,
        }
    params: dict[str, Any] = {"ts_code": sample_code, "limit": 3}
    if api.removesuffix("_vip") == "fina_mainbz":
        params["period"] = "20231231"
    return params


def _skip_result(api: str, reason: str) -> ApiValidationResult:
    return ApiValidationResult(api, "SKIP", 0, 0.0, notes=reason)


def validate_source(
    settings: Settings,
    *,
    sample_code: str = "600000.SH",
    include_vip: bool = False,
) -> ValidationReport:
    """Run one small request for each requested API; never logs credentials."""

    api_names = list(API_VALIDATION_ORDER)
    if include_vip:
        api_names.extend(VIP_API_NAMES)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not settings.token_configured:
        results = tuple(_skip_result(api, "TUSHARE_TOKEN is not configured") for api in api_names)
        return ValidationReport(tuple(results), sample_code, generated_at, False, include_vip)

    provider = TushareProvider(
        settings.token or "",
        settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_seconds,
    )
    results: list[ApiValidationResult] = []
    for api in api_names:
        start = time.monotonic()
        try:
            frame = provider.call(api, **_params_for(api, sample_code))
            duration = time.monotonic() - start
            fields = tuple(str(column) for column in frame.columns)
            spec = get_dataset_spec(api)
            missing = tuple(field for field in spec.required_fields if field not in frame.columns)
            if missing:
                status = "SCHEMA_MISMATCH"
                notes = f"required fields missing: {', '.join(missing)}"
            elif frame.empty:
                status = "EMPTY"
                notes = "request succeeded but returned no rows"
            else:
                status = "PASS"
                notes = "response returned and required fields were observed"
            results.append(ApiValidationResult(api, status, len(frame), duration, fields, notes))
        except ProviderError as exc:
            results.append(
                ApiValidationResult(
                    api,
                    "FAIL",
                    0,
                    time.monotonic() - start,
                    notes=exc.error_message,
                    error_type=exc.error_type,
                )
            )
    return ValidationReport(tuple(results), sample_code, generated_at, True, include_vip)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_validation_markdown(report: ValidationReport) -> str:
    ordinary = [result for result in report.results if not result.api.endswith("_vip")]
    vip = [result for result in report.results if result.api.endswith("_vip")]

    def table(results: list[ApiValidationResult]) -> str:
        lines = [
            "| API | Status | Rows | Duration (s) | Fields | Notes |",
            "| --- | --- | ---: | ---: | --- | --- |",
        ]
        for result in results:
            fields = ", ".join(result.fields) or "-"
            notes = result.notes
            if result.error_type:
                notes = f"error_type={result.error_type}; {notes}"
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(result.api),
                        _cell(result.status),
                        str(result.rows),
                        f"{result.duration_seconds:.3f}",
                        _cell(fields),
                        _cell(notes or "-"),
                    )
                )
                + " |"
            )
        return "\n".join(lines)

    lines = [
        "# Tushare-compatible data-source validation",
        "",
        f"- Generated at (UTC): `{report.generated_at}`",
        f"- Sample code: `{report.sample_code}`",
        f"- Token configured: `{report.token_configured}` (value never recorded)",
        "- Client: official Python `tushare` SDK only; optional Base URL override "
        "is confined to `TushareProvider`.",
        "- MCP and seller-specific HTTP APIs are not used by the data chain.",
        "",
        "## Ordinary APIs",
        "",
        table(ordinary),
        "",
        "## VIP APIs",
        "",
    ]
    if report.vip_requested:
        lines.append(table(vip))
    else:
        lines.append("Not run. Pass `--vip` to validate the optional VIP names separately.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `PASS` means the request returned and all minimal required fields were observed.",
            "- `EMPTY` means the request completed but the chosen sample/parameters "
            "returned no rows; it is not treated as schema proof.",
            "- `SCHEMA_MISMATCH` means the request completed but required fields were absent.",
            "- `FAIL` includes a classified provider error such as `timeout`, "
            "`connection`, `permission`, `not_found`, `rate_limit`, or `compatibility`.",
            "- With no token, all rows are deliberately `SKIP`; this is a "
            "credential/configuration block, not evidence that the endpoint is unavailable.",
            "",
            "## Pagination and field notes",
            "",
            "The validation calls request a small `limit`. Sample synchronization has a "
            "bounded limit/offset paginator with a maximum page count; it never retries "
            "indefinitely.",
            "",
            "## Run status",
            "",
            "- Ordinary API availability: `unknown` in this run because no token was configured.",
            "- VIP API availability: `not tested` unless the `--vip` option was used.",
            "- Live pagination behavior, live field presence, and live cumulative-value "
            "semantics: `unknown` until an authenticated sample run.",
            "",
        ]
    )
    return "\n".join(lines)


def render_pit_mapping_markdown(report: ValidationReport) -> str:
    """Render field mapping evidence without turning assumptions into facts."""

    by_api = {result.api.removesuffix("_vip"): result for result in report.results}
    lines = [
        "# Financial PIT field mapping",
        "",
        "The canonical PIT columns are `report_period`, `announcement_date`, "
        "`actual_available_date`, `report_type`, `update_flag`, `retrieved_at`, and `source`.",
        "A row with no usable `actual_available_date` is excluded from an as-of query; "
        "the implementation does not invent a date.",
        "",
        "| Dataset | report_period | announcement_date | available_date source | "
        "Field observation | Semantic status | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for dataset, mapping in PIT_MAPPINGS.items():
        result = by_api.get(dataset)
        observed = set(result.fields) if result else set()
        candidates = set(mapping.report_period_candidates + mapping.announcement_candidates)
        candidates.update(mapping.available_candidates)
        if mapping.disclosure_fallback:
            candidates.update({"actual_date", "disclosure_date"})
        raw_candidates = candidates.difference(
            {"report_period", "announcement_date", "actual_available_date"}
        )
        observed_candidates = sorted(
            candidate for candidate in raw_candidates if candidate in observed
        )
        if not report.token_configured:
            observation = "unknown: no live schema run"
            semantic_status = "unknown"
        elif observed_candidates:
            observation = "observed: " + ", ".join(observed_candidates)
            semantic_status = "suspected; field meaning still requires source confirmation"
        else:
            observation = "unknown: fields not observed in the sample response"
            semantic_status = "unknown"
        display_period = [
            candidate
            for candidate in mapping.report_period_candidates
            if candidate != "report_period"
        ]
        display_announcement = [
            candidate
            for candidate in mapping.announcement_candidates
            if candidate != "announcement_date"
        ]
        display_available = [
            candidate
            for candidate in mapping.available_candidates
            if candidate != "actual_available_date"
        ]
        available_source = " then ".join(display_available) or "disclosure_date.actual_date join"
        if mapping.disclosure_fallback:
            available_source = "disclosure_date.actual_date (explicit join only)"
        notes = (
            mapping.notes
            or "Retain report_type/update_flag and select the latest available "
            "version as of the query date."
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    dataset,
                    ", ".join(display_period),
                    ", ".join(display_announcement),
                    available_source,
                    observed_candidates and ", ".join(observed_candidates) or observation,
                    semantic_status,
                    _cell(notes),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Evidence status",
            "",
            "- `confirmed` is reserved for field presence and semantics established "
            "from a live response plus source documentation.",
            "- `suspected` means the implementation has a plausible field mapping "
            "but this run did not establish its semantic contract.",
            "- `unknown` means the required live evidence was unavailable; it must "
            "not be used as a backtest assumption.",
            "- `disclosure_date.actual_date` is deliberately not treated as "
            "availability for `fina_mainbz` without an explicit join. Its semantic "
            "meaning remains unknown until verified.",
            "",
            "## Version semantics",
            "",
            "`report_type` separates report families where present. `update_flag` "
            "is retained as a version attribute; PIT selection groups by report "
            "identity and picks the latest version whose `actual_available_date` "
            "is on or before `as_of_date`.",
            "",
            "## Quarterization scope",
            "",
            "The code contains only a prototype for cumulative `income`/`cashflow` "
            "values: Q1, H1-Q1, Q3-H1, and FY-Q3. Whether each live endpoint's "
            "values are cumulative is `unknown` until the real API sample and "
            "source semantics are verified.",
            "",
        ]
    )
    return "\n".join(lines)


def write_validation_report(report: ValidationReport, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_validation_markdown(report), encoding="utf-8")


def write_pit_mapping_report(report: ValidationReport, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_pit_mapping_markdown(report), encoding="utf-8")
