"""Thin wrapper around the official Tushare Python SDK."""

from __future__ import annotations

import json
import logging
import math
import random
import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import requests
import tushare as ts

from ..security import redact_text
from .rate_limit import RateLimiter

LOGGER = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A classified, sanitized error from a Tushare-compatible endpoint."""

    def __init__(
        self,
        api_name: str,
        error_type: str,
        message: str,
        *,
        attempts: int,
    ) -> None:
        self.api_name = api_name
        self.error_type = error_type
        self.attempts = attempts
        self.error_message = message
        super().__init__(f"{api_name} failed ({error_type}) after {attempts} attempt(s): {message}")


_RETRYABLE_ERROR_TYPES = {
    "connection",
    "timeout",
    "rate_limit",
    "server_error",
    "compatibility",
}
_TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _redact(text: str, secret: str | None = None) -> str:
    """Return provider error text without secrets or private endpoint URLs."""

    return redact_text(text, secret)


def _classify_error(exc: BaseException) -> str:
    """Map SDK, HTTP and proxy failures into stable operational categories."""

    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection"

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {401, 403}:
        return "permission"
    if status_code == 404:
        return "not_found"
    if status_code in _TRANSIENT_HTTP_STATUS_CODES:
        return "rate_limit" if status_code == 429 else "server_error"
    if status_code is not None and 400 <= status_code < 500:
        return "http_error"
    if isinstance(exc, requests.RequestException):
        return "connection"

    text = str(exc).lower()
    if any(
        term in text
        for term in (
            "429",
            "rate limit",
            "每分钟最多",
            "访问过于频繁",
            "频繁",
            "限流",
            "too many",
        )
    ):
        return "rate_limit"
    if any(
        term in text
        for term in (
            "500",
            "502",
            "503",
            "504",
            "internal server error",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "temporarily unavailable",
            "temporary failure",
            "系统繁忙",
            "服务异常",
            "服务器错误",
        )
    ):
        return "server_error"
    if any(
        term in text
        for term in (
            "permission",
            "privilege",
            "unauthorized",
            "forbidden",
            "没有权限",
            "权限",
            "积分",
            "token无效",
            "token不对",
            "token invalid",
        )
    ):
        return "permission"
    if any(
        term in text
        for term in (
            "not found",
            "unknown api",
            "invalid api",
            "接口不存在",
            "不存在该接口",
            "请指定正确的接口名",
            "请指定正确的接口",
            "404",
        )
    ):
        return "not_found"
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError)):
        return "compatibility"
    if any(
        term in text
        for term in ("json", "html", "字段", "必填参数", "响应格式", "response format")
    ):
        return "compatibility"
    return "unknown"


class TushareProvider:
    """One construction and call boundary for the official Tushare SDK.

    ``base_url`` is intentionally applied through the SDK's private transport
    attribute because the current SDK does not expose a public base-URL setter.
    No other project module constructs a Tushare client or touches that field.
    """

    def __init__(
        self,
        token: str,
        base_url: str | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        backoff_jitter_seconds: float = 0.0,
        rate_limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        logger: logging.Logger | None = None,
    ) -> None:
        if not token or not token.strip():
            raise ValueError("TUSHARE_TOKEN is required to construct TushareProvider")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if not math.isfinite(backoff_seconds) or backoff_seconds < 0:
            raise ValueError("backoff_seconds must be finite and non-negative")
        if not math.isfinite(backoff_jitter_seconds) or backoff_jitter_seconds < 0:
            raise ValueError("backoff_jitter_seconds must be finite and non-negative")

        self._token = token.strip()
        self._timeout = timeout
        self._base_url = base_url.strip() if base_url and base_url.strip() else None
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._backoff_jitter_seconds = backoff_jitter_seconds
        self._rate_limiter = rate_limiter
        self._sleep = sleep
        self._random = random_fn
        self._logger = logger or LOGGER

        # This is the sole Tushare client construction in the repository.
        self.pro = ts.pro_api(self._token)
        if hasattr(self.pro, "_DataApi__timeout"):
            self.pro._DataApi__timeout = timeout
        if self._base_url:
            # The SDK currently appends ``/{api_name}``; preserve the supplied
            # setting exactly so the documented compatibility endpoint works.
            self.pro._DataApi__http_url = self._base_url

    @property
    def base_url_configured(self) -> bool:
        return self._base_url is not None

    @property
    def endpoint_kind(self) -> str:
        return "custom" if self._base_url else "official"

    @property
    def transport_base_url(self) -> str | None:
        """Expose the configured SDK transport URL without leaking credentials."""

        return getattr(self.pro, "_DataApi__http_url", None)

    @property
    def rate_limiter(self) -> RateLimiter | None:
        return self._rate_limiter

    def set_rate_limiter(self, rate_limiter: RateLimiter | None) -> None:
        """Attach the shared limiter before concurrent calls begin."""

        self._rate_limiter = rate_limiter

    def __repr__(self) -> str:
        return (
            f"TushareProvider(endpoint_kind={self.endpoint_kind!r}, "
            f"base_url_configured={self.base_url_configured!r}, "
            f"max_retries={self._max_retries!r})"
        )

    def call(self, api_name: str, *, fields: str | None = None, **params: Any) -> pd.DataFrame:
        """Call one API with bounded retry and a consistent DataFrame result."""

        if not api_name or not api_name.strip():
            raise ValueError("api_name must not be empty")
        query_params = dict(params)
        if fields is not None:
            query_params["fields"] = fields

        for attempt in range(1, self._max_retries + 2):
            started = time.monotonic()
            try:
                if self._rate_limiter is not None:
                    self._rate_limiter.acquire()
                result = self.pro.query(api_name, **query_params)
                if result is None:
                    frame = pd.DataFrame()
                elif isinstance(result, pd.DataFrame):
                    frame = result
                else:
                    frame = pd.DataFrame(result)
                elapsed = time.monotonic() - started
                if frame.empty:
                    self._logger.warning(
                        "tushare_request api=%s rows=0 elapsed=%.3f attempt=%d",
                        api_name,
                        elapsed,
                        attempt,
                    )
                else:
                    self._logger.info(
                        "tushare_request api=%s rows=%d elapsed=%.3f attempt=%d",
                        api_name,
                        len(frame),
                        elapsed,
                        attempt,
                    )
                return frame.reset_index(drop=True)
            except Exception as exc:  # SDK raises plain Exception for API errors.
                error_type = _classify_error(exc)
                message = _redact(str(exc), self._token)
                retryable = error_type in _RETRYABLE_ERROR_TYPES
                if retryable and attempt <= self._max_retries:
                    delay = self._backoff_seconds * (2 ** (attempt - 1))
                    if self._backoff_jitter_seconds:
                        delay += self._backoff_jitter_seconds * self._random()
                    self._logger.warning(
                        "Tushare API call will retry: api=%s error_type=%s attempt=%d",
                        api_name,
                        error_type,
                        attempt,
                    )
                    self._sleep(delay)
                    continue
                self._logger.error(
                    "tushare_request_failed api=%s error_type=%s attempt=%d message=%s",
                    api_name,
                    error_type,
                    attempt,
                    message,
                )
                raise ProviderError(
                    api_name,
                    error_type,
                    message,
                    attempts=attempt,
                ) from exc

        raise AssertionError("unreachable")
