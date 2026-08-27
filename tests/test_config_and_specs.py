from __future__ import annotations

from ashare_turnaround.config import DEFAULT_BASE_URL, Settings
from ashare_turnaround.datasets.specs import API_VALIDATION_ORDER, get_dataset_spec


def test_settings_redact_token_from_repr_and_support_official_switch() -> None:
    settings = Settings(token="secret-token", base_url="")
    assert settings.token_configured
    assert settings.base_url is None
    assert "secret-token" not in repr(settings)
    assert DEFAULT_BASE_URL.startswith("https://")


def test_settings_repr_does_not_expose_credentials_in_endpoint_url() -> None:
    settings = Settings(
        token="secret-token",
        base_url="https://private.example/api?token=secret-token",
    )

    assert "private.example" not in repr(settings)
    assert "secret-token" not in repr(settings)


def test_specs_capture_raw_and_pit_fields() -> None:
    income = get_dataset_spec("income")
    assert income.api_name == "income"
    assert "f_ann_date" in income.pit_fields
    assert income.partition_strategy == "year"
    assert get_dataset_spec("trade_cal").partition_strategy == "year"
    assert get_dataset_spec("trade_cal").partition_field == "cal_date"
    assert "update_flag" in get_dataset_spec("express").pit_fields
    assert "curr_type" in get_dataset_spec("fina_mainbz").primary_keys
    assert set(API_VALIDATION_ORDER) >= {"income", "disclosure_date"}
    assert get_dataset_spec("income_vip").api_name == "income_vip"
