from __future__ import annotations

import json
import logging

import pandas as pd
import pytest

import ashare_turnaround.providers.tushare as provider_module
from ashare_turnaround.providers.tushare import ProviderError, TushareProvider
from ashare_turnaround.storage.state import SyncRecord, SyncStateStore


class ErrorClient:
    def query(self, api_name: str, **params: object) -> pd.DataFrame:
        raise RuntimeError(
            "request https://private.example/mcp/query Authorization: Bearer "
            "secret-token"
        )


def test_provider_error_redacts_token_authorization_and_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: ErrorClient())
    provider = TushareProvider(
        "secret-token", max_retries=0, logger=logging.getLogger("security-provider")
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ProviderError) as error:
            provider.call("income")

    rendered = str(error.value) + caplog.text
    assert "secret-token" not in rendered
    assert "private.example" not in rendered
    assert "Authorization: Bearer" not in rendered
    assert "<redacted-url>" in str(error.value)


def test_state_redacts_sensitive_keys_and_private_urls(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = SyncStateStore(path, secret="secret-token")
    state.append(
        SyncRecord(
            dataset="income",
            params={
                "authorization": "Bearer secret-token",
                "endpoint": "https://private.example/mcp",
                "ts_code": "600000.SH",
            },
            started_at="now",
            finished_at="now",
            status="failed",
            error_type="unknown",
            error_message="request https://private.example/mcp failed with secret-token",
        )
    )

    raw = path.read_text(encoding="utf-8")
    assert "secret-token" not in raw
    assert "private.example" not in raw
    assert "Bearer secret-token" not in raw
    assert json.loads(raw)[0]["params"]["ts_code"] == "600000.SH"
