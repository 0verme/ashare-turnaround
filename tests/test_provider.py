from __future__ import annotations

import logging

import pandas as pd
import pytest
import requests

import ashare_turnaround.providers.tushare as provider_module
from ashare_turnaround.providers.rate_limit import RateLimiter
from ashare_turnaround.providers.tushare import ProviderError, TushareProvider


class FakeClient:
    def __init__(self, token: str, failures: list[BaseException] | None = None) -> None:
        self.token = token
        self.failures = list(failures or [])
        self.calls: list[tuple[str, dict[str, object]]] = []

    def query(self, api_name: str, **params: object) -> pd.DataFrame:
        self.calls.append((api_name, params))
        if self.failures:
            failure = self.failures.pop(0)
            raise failure
        return pd.DataFrame({"ts_code": ["600000.SH"], "value": [1.0]})


def test_base_url_override_and_call_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient("secret-token")
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: client)

    provider = TushareProvider("secret-token", "https://proxy.example/", max_retries=0)
    result = provider.call("income", ts_code="600000.SH", limit=1)

    assert provider.transport_base_url == "https://proxy.example/"
    assert result.to_dict("records") == [{"ts_code": "600000.SH", "value": 1.0}]
    assert client.calls == [("income", {"ts_code": "600000.SH", "limit": 1})]
    assert "secret-token" not in repr(provider)


def test_retry_is_bounded_and_exponential(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient(
        "secret-token", [requests.Timeout("temporary"), requests.ConnectionError("down")]
    )
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: client)
    delays: list[float] = []

    provider = TushareProvider(
        "secret-token",
        max_retries=2,
        backoff_seconds=0.25,
        sleep=delays.append,
    )
    result = provider.call("daily")

    assert len(result) == 1
    assert delays == [0.25, 0.5]
    assert len(client.calls) == 3


def test_transient_http_server_error_retries_with_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    response = requests.Response()
    response.status_code = 503
    client = FakeClient("secret-token", [requests.HTTPError("busy", response=response)])
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: client)
    delays: list[float] = []

    provider = TushareProvider(
        "secret-token",
        max_retries=1,
        backoff_seconds=0.25,
        backoff_jitter_seconds=0.1,
        random_fn=lambda: 0.5,
        sleep=delays.append,
    )
    result = provider.call("income")

    assert len(result) == 1
    assert delays == [0.3]
    assert len(client.calls) == 2


def test_provider_rate_limits_each_retry_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient("secret-token", [requests.Timeout("temporary")])
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: client)
    limiter = RateLimiter(6_000_000.0)

    provider = TushareProvider(
        "secret-token",
        max_retries=1,
        backoff_seconds=0,
        rate_limiter=limiter,
        sleep=lambda _: None,
    )
    provider.call("income")

    assert limiter.request_count == 2
    assert len(client.calls) == 2


def test_error_is_classified_and_token_redacted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = FakeClient("secret-token", [Exception("permission denied for secret-token")])
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: client)
    provider = TushareProvider(
        "secret-token", max_retries=3, logger=logging.getLogger("test-provider")
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ProviderError) as error:
            provider.call("income")

    assert error.value.error_type == "permission"
    assert "secret-token" not in str(error.value)
    assert "secret-token" not in caplog.text
    assert len(client.calls) == 1


def test_provider_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_module.ts, "pro_api", lambda token: FakeClient(token))
    with pytest.raises(ValueError):
        TushareProvider("")
