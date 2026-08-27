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
from .quality import check_frame_quality, compare_field_sets

VIP_PROBE_PERIOD = "20231231"
_CANONICAL_PIT_FIELDS = {
    "report_period",
    "announcement_date",
    "actual_available_date",
    "available_date_source",
    "retrieved_at",
    "source",
}


@dataclass(frozen=True, slots=True)
class ApiValidationResult:
    api: str
    status: str
    rows: int
    duration_seconds: float
    fields: tuple[str, ...] = ()
    notes: str = ""
    error_type: str | None = None
    full_market_by_period: str = "NOT_TESTED"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    results: tuple[ApiValidationResult, ...]
    sample_code: str
    generated_at: str
    token_configured: bool
    vip_requested: bool = False

    @property
    def ordinary_results(self) -> tuple[ApiValidationResult, ...]:
        return tuple(result for result in self.results if not result.api.endswith("_vip"))

    @property
    def vip_results(self) -> tuple[ApiValidationResult, ...]:
        return tuple(result for result in self.results if result.api.endswith("_vip"))

    @property
    def core_failures(self) -> tuple[ApiValidationResult, ...]:
        """Return authenticated core results that are not successful."""

        return tuple(
            result
            for result in self.ordinary_results
            if result.api in CORE_DATASETS
            and result.status in {"FAIL", "PERMISSION", "NOT_SUPPORTED", "SCHEMA_MISMATCH", "EMPTY"}
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
    if api in VIP_API_NAMES:
        # This is intentionally a three-row, no-ts_code probe of the mode that
        # could support a Phase 2 report-period bootstrap.
        return {"period": VIP_PROBE_PERIOD, "limit": 3}
    params: dict[str, Any] = {"ts_code": sample_code, "limit": 3}
    if api.removesuffix("_vip") == "fina_mainbz":
        params["period"] = "20231231"
    return params


def _skip_result(api: str, reason: str) -> ApiValidationResult:
    return ApiValidationResult(api, "SKIP", 0, 0.0, notes=reason)


def _status_for_provider_error(error_type: str) -> str:
    if error_type == "permission":
        return "PERMISSION"
    if error_type == "not_found":
        return "NOT_SUPPORTED"
    return "FAIL"


def _frame_result(
    api: str,
    frame: Any,
    duration_seconds: float,
    *,
    params: dict[str, Any],
) -> ApiValidationResult:
    fields = tuple(str(column) for column in frame.columns)
    spec = get_dataset_spec(api)
    quality = check_frame_quality(api, frame, spec)
    if frame.empty:
        status = "EMPTY"
        notes = "request succeeded but returned no rows; schema is not proven"
    elif quality.missing_required:
        status = "SCHEMA_MISMATCH"
        notes = f"required fields missing: {', '.join(quality.missing_required)}"
    else:
        status = "PASS"
        notes = "response returned and required fields were observed"

    if quality.warnings:
        notes += "; quality_warnings=" + ",".join(quality.warnings)

    full_market = "NOT_TESTED"
    if api in VIP_API_NAMES:
        notes = (
            f"bounded period probe period={params.get('period')} without ts_code; " + notes
        )
        if status == "PASS" and "ts_code" in frame.columns:
            full_market = "YES (bounded period probe)"
        elif status == "EMPTY":
            full_market = "UNKNOWN (period probe returned no rows)"
        elif status == "SCHEMA_MISMATCH":
            full_market = "NO (required fields missing)"
        else:
            full_market = "NOT_CONFIRMED"
    return ApiValidationResult(
        api,
        status,
        len(frame),
        duration_seconds,
        fields,
        notes,
        full_market_by_period=full_market,
    )


def validate_source(
    settings: Settings,
    *,
    sample_code: str = "600000.SH",
    include_vip: bool = False,
) -> ValidationReport:
    """Run one bounded request for each requested API; never logs credentials."""

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
        params = _params_for(api, sample_code)
        start = time.monotonic()
        try:
            frame = provider.call(api, **params)
            results.append(_frame_result(api, frame, time.monotonic() - start, params=params))
        except ProviderError as exc:
            status = _status_for_provider_error(exc.error_type)
            full_market = "NOT_CONFIRMED" if api in VIP_API_NAMES else "NOT_TESTED"
            results.append(
                ApiValidationResult(
                    api,
                    status,
                    0,
                    time.monotonic() - start,
                    notes=exc.error_message,
                    error_type=exc.error_type,
                    full_market_by_period=full_market,
                )
            )
    return ValidationReport(tuple(results), sample_code, generated_at, True, include_vip)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _status_summary(results: tuple[ApiValidationResult, ...], token_configured: bool) -> str:
    if not token_configured:
        return "NOT RUN — token not configured"
    if not results:
        return "NOT TESTED"
    passed = sum(result.status == "PASS" for result in results)
    if passed == len(results):
        return "PASS"
    if passed:
        return f"PARTIAL — {passed}/{len(results)} PASS"
    statuses = {result.status for result in results}
    if statuses == {"EMPTY"}:
        return "EMPTY"
    if "PERMISSION" in statuses:
        return "PERMISSION"
    if "NOT_SUPPORTED" in statuses:
        return "NOT SUPPORTED"
    return "FAIL"


def _field_presence_summary(report: ValidationReport) -> str:
    if not report.token_configured:
        return "NOT RUN — token not configured"
    ordinary = report.ordinary_results
    if ordinary and all(result.status == "PASS" for result in ordinary):
        return "PASS for validated samples"
    if any(result.status == "SCHEMA_MISMATCH" for result in ordinary):
        return "FAIL — at least one required schema field was missing"
    if any(result.status == "PASS" for result in ordinary):
        return "PARTIAL — empty/error responses do not prove schema"
    return "NOT CONFIRMED"


def _pagination_summary(report: ValidationReport) -> str:
    if not report.token_configured:
        return "NOT RUN — token not configured"
    if any(result.status == "PASS" for result in report.ordinary_results):
        return (
            "PARTIAL — bounded sample paginator validated; not yet proven for every "
            "endpoint/full historical ranges"
        )
    return "NOT CONFIRMED"


def _cumulative_semantics_summary(report: ValidationReport) -> str:
    if not report.token_configured:
        return "NOT RUN — token not configured"
    by_api = {result.api: result for result in report.ordinary_results}
    if all(by_api.get(api) and by_api[api].status == "PASS" for api in ("income", "cashflow")):
        return (
            "PARTIAL — quarterization prototype checked; live cumulative semantics "
            "not fully confirmed"
        )
    return "NOT CONFIRMED — income/cashflow live evidence is incomplete"


def render_validation_markdown(report: ValidationReport) -> str:
    ordinary = list(report.ordinary_results)
    vip = list(report.vip_results)

    def table(results: list[ApiValidationResult]) -> str:
        lines = [
            "| API | Status | Rows | Duration (s) | Fields | Error Type | Notes |",
            "| --- | --- | ---: | ---: | --- | --- | --- |",
        ]
        for result in results:
            fields = ", ".join(result.fields) or "-"
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(result.api),
                        _cell(result.status),
                        str(result.rows),
                        f"{result.duration_seconds:.3f}",
                        _cell(fields),
                        _cell(result.error_type or "-"),
                        _cell(result.notes or "-"),
                    )
                )
                + " |"
            )
        return "\n".join(lines)

    ordinary_pass = sum(result.status == "PASS" for result in ordinary)
    vip_summary = (
        "NOT TESTED"
        if not report.vip_requested
        else _status_summary(tuple(vip), report.token_configured)
    )
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
        lines.append("Not run. Pass `--vip` to validate the bounded report-period probes.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `PASS` means the request returned and all minimal required fields were observed.",
            "- `EMPTY` means the request completed but the chosen sample/parameters "
            "returned no rows; it is not treated as schema proof.",
            "- `PERMISSION` and `NOT_SUPPORTED` are classified endpoint outcomes; "
            "`SCHEMA_MISMATCH` means required fields were absent.",
            "- `FAIL` includes a classified provider error such as `timeout`, "
            "`connection`, `rate_limit`, or `compatibility`.",
            "- With no token, all rows are deliberately `SKIP`; this is a "
            "credential/configuration block, not evidence that the endpoint is unavailable.",
            "",
            "## Pagination and field notes",
            "",
            "The ordinary validation calls request a small `limit`. VIP validation uses a "
            f"bounded `period={VIP_PROBE_PERIOD}` request without `ts_code`; neither proves "
            "complete pagination or full historical coverage.",
            "",
            "## Run status",
            "",
            "- Ordinary API availability: "
            f"`{_status_summary(tuple(ordinary), report.token_configured)}`",
            f"- Validated ordinary APIs: `{ordinary_pass} / {len(ordinary)} PASS`",
            f"- Live field presence: `{_field_presence_summary(report)}`",
            f"- Live pagination: `{_pagination_summary(report)}`",
            f"- Live cumulative financial semantics: `{_cumulative_semantics_summary(report)}`",
            f"- VIP API availability: `{vip_summary}`",
            "",
        ]
    )
    return "\n".join(lines)


def render_pit_mapping_markdown(report: ValidationReport) -> str:
    """Render field mapping evidence without turning assumptions into facts."""

    by_api = {result.api: result for result in report.ordinary_results}
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
        spec = get_dataset_spec(dataset)
        candidates.update(spec.pit_fields)
        raw_candidates = candidates.difference(_CANONICAL_PIT_FIELDS)
        observed_candidates = sorted(
            candidate for candidate in raw_candidates if candidate in observed
        )
        if not report.token_configured:
            observation = "unknown: no live schema run"
            semantic_status = "unknown"
        elif observed_candidates:
            observation = "observed: " + ", ".join(observed_candidates)
            semantic_status = mapping.semantic_status or "suspected"
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
                    _cell(observation),
                    _cell(semantic_status),
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
            "but the semantic contract is not fully established.",
            "- `unknown` means the required live evidence was unavailable, or the "
            "event field has not been proven to be data availability.",
            "- `disclosure_date.actual_date` is deliberately not treated as "
            "availability for `fina_mainbz` without an explicit join. Its semantic "
            "meaning remains unknown until verified.",
            "",
            "## Version semantics",
            "",
            "`report_type` separates report families where present. `update_flag` "
            "is retained as a version attribute; PIT selection groups by report "
            "identity and picks the latest version whose `actual_available_date` "
            "is on or before the `as_of_date`.",
            "",
            "## Quarterization scope",
            "",
            "The code contains only a prototype for cumulative `income`/`cashflow` "
            "values: Q1, H1-Q1, Q3-H1, and FY-Q3. Live bounded checks are recorded "
            "in `docs/pit-validation.md`; this is not a factor calculation.",
            "",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class VipEvaluation:
    dataset: str
    ordinary_status: str
    vip_status: str
    full_market_by_period: str
    schema_relation: str
    missing_vip_pit_fields: tuple[str, ...]
    recommendation: str
    notes: str


def _raw_pit_fields(dataset: str) -> set[str]:
    return set(get_dataset_spec(dataset).pit_fields).difference(_CANONICAL_PIT_FIELDS)


def evaluate_vip_apis(report: ValidationReport) -> tuple[VipEvaluation, ...]:
    """Compare VIP responses with ordinary schemas without silently merging them."""

    ordinary = {result.api: result for result in report.ordinary_results}
    vip = {result.api: result for result in report.vip_results}
    evaluations: list[VipEvaluation] = []
    for api in VIP_API_NAMES:
        dataset = api.removesuffix("_vip")
        ordinary_result = ordinary.get(dataset)
        vip_result = vip.get(api)
        ordinary_status = ordinary_result.status if ordinary_result else "NOT TESTED"
        if not report.vip_requested or not report.token_configured or vip_result is None:
            evaluations.append(
                VipEvaluation(
                    dataset,
                    ordinary_status,
                    "NOT TESTED",
                    "NOT TESTED",
                    "unknown",
                    (),
                    "ordinary",
                    "VIP period probe was not run",
                )
            )
            continue

        relation = (
            compare_field_sets(vip_result.fields, ordinary_result.fields)
            if ordinary_result
            else "unknown"
        )
        missing = tuple(sorted(_raw_pit_fields(dataset).difference(vip_result.fields)))
        full_market = vip_result.full_market_by_period
        if (
            vip_result.status == "PASS"
            and full_market.startswith("YES")
            and relation in {"same", "superset"}
            and not missing
            and PIT_MAPPINGS[dataset].semantic_status == "confirmed"
        ):
            recommendation = "VIP"
        elif ordinary_status == "PASS":
            recommendation = "fallback"
        else:
            recommendation = "ordinary"
        notes_parts = [
            f"schema={relation}",
            f"pit_mapping={PIT_MAPPINGS[dataset].semantic_status}",
        ]
        if dataset == "fina_mainbz":
            notes_parts.append("requires explicit disclosure_date join")
        if missing:
            notes_parts.append("missing_vip_pit_fields=" + ",".join(missing))
        if vip_result.error_type:
            notes_parts.append(f"error_type={vip_result.error_type}")
        evaluations.append(
            VipEvaluation(
                dataset,
                ordinary_status,
                vip_result.status,
                full_market,
                relation,
                missing,
                recommendation,
                "; ".join(notes_parts),
            )
        )
    return tuple(evaluations)


def render_vip_evaluation_markdown(report: ValidationReport) -> str:
    evaluations = evaluate_vip_apis(report)
    lines = [
        "# VIP API evaluation",
        "",
        f"- Generated at (UTC): `{report.generated_at}`",
        f"- Token configured: `{report.token_configured}` (value never recorded)",
        "- VIP calls are bounded `period=20231231` probes without `ts_code`; a positive "
        "result establishes the query mode, not complete pagination or historical coverage.",
        "",
        "| Dataset | Ordinary | VIP | Full-market capable | Recommendation |",
        "| --- | --- | --- | --- | --- |",
    ]
    for evaluation in evaluations:
        lines.append(
            "| "
            + " | ".join(
                (
                    evaluation.dataset,
                    _cell(evaluation.ordinary_status),
                    _cell(evaluation.vip_status),
                    _cell(evaluation.full_market_by_period),
                    _cell(evaluation.recommendation),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Schema and PIT notes",
            "",
            "| Dataset | VIP schema vs ordinary | Missing VIP PIT fields | Notes |",
            "| --- | --- | --- | --- |",
        ]
    )
    for evaluation in evaluations:
        missing = ", ".join(evaluation.missing_vip_pit_fields) or "-"
        lines.append(
            "| "
            + " | ".join(
                (
                    evaluation.dataset,
                    _cell(evaluation.schema_relation),
                    _cell(missing),
                    _cell(evaluation.notes),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Recommendation rule",
            "",
            "`VIP` requires a successful bounded period probe, an identical or superset "
            "schema, no missing raw PIT fields, and a confirmed PIT mapping. `fallback` "
            "means ordinary remains "
            "usable but VIP is not safe as the primary source. `ordinary` means VIP "
            "was not tested or ordinary itself was not ready.",
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


def write_vip_evaluation_report(report: ValidationReport, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_vip_evaluation_markdown(report), encoding="utf-8")
