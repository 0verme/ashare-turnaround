from __future__ import annotations

import os

import pytest

from ashare_turnaround.config import load_settings
from ashare_turnaround.validation import validate_source


@pytest.mark.integration
def test_live_source_matrix_is_opt_in() -> None:
    if os.getenv("ASHARE_RUN_INTEGRATION") != "1":
        pytest.skip("set ASHARE_RUN_INTEGRATION=1 to run live source validation")
    settings = load_settings()
    if not settings.token_configured:
        pytest.skip("TUSHARE_TOKEN is not configured")
    report = validate_source(settings)
    assert not report.core_failures
