"""Environment-backed application configuration."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# When TUSHARE_BASE_URL is unset, ``None`` defers to the official Tushare SDK
# default endpoint. No private/compatible endpoint is hard-coded in source.
DEFAULT_BASE_URL: str | None = None
DEFAULT_DATA_DIR = Path("./data")
DEFAULT_BENCHMARK_CODE = "000300.SH"
SOURCE_NAME = "tushare-compatible"


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings without putting credentials in logs or repr output."""

    token: str | None = field(default=None, repr=False)
    base_url: str | None = field(default=DEFAULT_BASE_URL, repr=False)
    data_dir: Path = DEFAULT_DATA_DIR
    benchmark_code: str = DEFAULT_BENCHMARK_CODE
    timeout: float = 30.0
    max_retries: int = 2
    backoff_seconds: float = 1.0
    backoff_jitter_seconds: float = 0.25
    requests_per_minute: float = 60.0

    def __post_init__(self) -> None:
        if self.token is not None:
            object.__setattr__(self, "token", self.token.strip() or None)
        if self.base_url is not None:
            object.__setattr__(self, "base_url", self.base_url.strip() or None)
        object.__setattr__(self, "data_dir", Path(self.data_dir).expanduser())
        benchmark = str(self.benchmark_code).strip().upper()
        if not benchmark or "." not in benchmark:
            raise ValueError("benchmark_code must be a non-empty Tushare symbol")
        object.__setattr__(self, "benchmark_code", benchmark)
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        if not math.isfinite(self.backoff_jitter_seconds) or self.backoff_jitter_seconds < 0:
            raise ValueError("backoff_jitter_seconds must be finite and non-negative")
        if not math.isfinite(self.requests_per_minute) or self.requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be a finite positive number")

    @property
    def token_configured(self) -> bool:
        return bool(self.token)

    def ensure_data_dirs(self) -> None:
        """Create only local runtime directories; data remains git-ignored."""

        for name in ("raw", "derived", "state", "reports"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)


def load_settings(env_file: str | Path | None = None) -> Settings:
    """Load ``.env`` from the current project directory and return settings.

    Existing process environment variables take precedence.  An explicitly
    supplied ``env_file`` is useful for tests and operators running elsewhere.
    """

    dotenv_path = Path(env_file) if env_file is not None else Path.cwd() / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=False)

    token = os.getenv("TUSHARE_TOKEN") or None
    raw_base_url = os.getenv("TUSHARE_BASE_URL")
    base_url = (raw_base_url or "").strip() or None
    raw_data_dir = os.getenv("ASHARE_DATA_DIR", str(DEFAULT_DATA_DIR))
    benchmark_code = os.getenv("ASHARE_BENCHMARK_CODE", DEFAULT_BENCHMARK_CODE)

    return Settings(
        token=token,
        base_url=base_url,
        data_dir=Path(raw_data_dir),
        benchmark_code=benchmark_code,
        timeout=float(os.getenv("TUSHARE_TIMEOUT", "30")),
        max_retries=int(os.getenv("TUSHARE_MAX_RETRIES", "2")),
        backoff_seconds=float(os.getenv("TUSHARE_BACKOFF_SECONDS", "1")),
        backoff_jitter_seconds=float(os.getenv("TUSHARE_BACKOFF_JITTER_SECONDS", "0.25")),
        requests_per_minute=float(os.getenv("TUSHARE_REQUESTS_PER_MINUTE", "60")),
    )
