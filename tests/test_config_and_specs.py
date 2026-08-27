from __future__ import annotations

from ashare_turnaround.config import DEFAULT_BASE_URL, Settings
from ashare_turnaround.datasets.specs import API_VALIDATION_ORDER, get_dataset_spec


def test_settings_redact_token_from_repr_and_support_official_switch() -> None:
    settings = Settings(token="secret-token", base_url="")
    assert settings.token_configured
    assert settings.base_url is None
    assert "secret-token" not in repr(settings)
    assert DEFAULT_BASE_URL.startswith("https://")


def test_specs_capture_raw_and_pit_fields() -> None:
    income = get_dataset_spec("income")
    assert income.api_name == "income"
    assert "f_ann_date" in income.pit_fields
    assert income.partition_strategy == "year"
    assert set(API_VALIDATION_ORDER) >= {"income", "disclosure_date"}
    assert get_dataset_spec("income_vip").api_name == "income_vip"
