from __future__ import annotations

from ashare_turnaround.validation import (
    ApiValidationResult,
    ValidationReport,
    render_validation_markdown,
    render_vip_evaluation_markdown,
)

ORDINARY_APIS = (
    "stock_basic",
    "trade_cal",
    "daily",
    "daily_basic",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
    "fina_mainbz",
    "forecast",
    "express",
    "fina_audit",
    "disclosure_date",
)


def _result(api: str, status: str = "PASS", *, fields: tuple[str, ...] = ()) -> ApiValidationResult:
    return ApiValidationResult(api, status, 3 if status == "PASS" else 0, 0.1, fields)


def test_validation_summary_matches_all_pass_matrix() -> None:
    report = ValidationReport(
        tuple(_result(api) for api in ORDINARY_APIS),
        "600000.SH",
        "2026-01-01T00:00:00Z",
        True,
    )

    rendered = render_validation_markdown(report)

    assert "Ordinary API availability: `PASS`" in rendered
    assert "Validated ordinary APIs: `13 / 13 PASS`" in rendered
    assert "no token was configured" not in rendered
    assert "VIP API availability: `NOT TESTED`" in rendered


def test_vip_status_and_error_type_are_rendered_separately() -> None:
    results = tuple(_result(api) for api in ORDINARY_APIS) + (
        ApiValidationResult(
            "income_vip",
            "PERMISSION",
            0,
            0.2,
            error_type="permission",
            notes="permission denied",
            full_market_by_period="NOT_CONFIRMED",
        ),
    )
    report = ValidationReport(results, "600000.SH", "now", True, True)

    rendered = render_validation_markdown(report)
    evaluation = render_vip_evaluation_markdown(report)

    assert "| API | Status | Rows | Duration (s) | Fields | Error Type | Notes |" in rendered
    assert (
        "| income_vip | PERMISSION | 0 | 0.200 | - | permission | permission denied |"
        in rendered
    )
    assert "VIP API availability: `PERMISSION`" in rendered
    assert "| income | PASS | PERMISSION | NOT_CONFIRMED | fallback |" in evaluation
