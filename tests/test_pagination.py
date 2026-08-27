from __future__ import annotations

import pandas as pd
import pytest

from ashare_turnaround.datasets.sync import (
    PaginationError,
    _fetch_and_record,
    fetch_paginated,
    fetch_paginated_audited,
)
from ashare_turnaround.providers.tushare import ProviderError
from ashare_turnaround.storage.state import SyncStateStore


class PageProvider:
    def __init__(self, pages: dict[int, pd.DataFrame | BaseException]) -> None:
        self.pages = pages
        self.calls: list[dict[str, object]] = []

    def call(self, dataset: str, **params: object) -> pd.DataFrame:
        self.calls.append({"dataset": dataset, **params})
        page = self.pages[int(params["offset"])]
        if isinstance(page, BaseException):
            raise page
        return page.copy()


def _page(start: int, count: int, *, extra: bool = False) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "ts_code": [f"60000{value}.SH" for value in range(start, start + count)],
            "trade_date": [f"202401{value:02d}" for value in range(start, start + count)],
            "close": [float(value) for value in range(start, start + count)],
        }
    )
    if extra:
        frame["volume"] = 100
    return frame


def test_pagination_uses_offsets_and_stops_at_a_short_terminal_page() -> None:
    provider = PageProvider({0: _page(1, 2), 2: _page(3, 1)})

    result = fetch_paginated_audited(
        provider, "daily", {"limit": 2}, page_size=2, max_pages=3
    )

    assert result.status == "PASS"
    assert result.page_count == 2
    assert [call["offset"] for call in provider.calls] == [0, 2]
    assert result.frame["ts_code"].tolist() == ["600001.SH", "600002.SH", "600003.SH"]


def test_empty_response_is_recorded_as_empty_not_success(tmp_path) -> None:
    provider = PageProvider({0: pd.DataFrame()})
    state = SyncStateStore(tmp_path / "state.json")

    result = _fetch_and_record(
        provider,
        "daily",
        {"limit": 2},
        state,
        page_size=2,
        max_pages=2,
    )

    assert result.status == "empty"
    assert state.latest("daily")["status"] == "empty"


def test_full_page_at_max_pages_is_partial_not_pass() -> None:
    provider = PageProvider({0: _page(1, 2), 2: _page(3, 2)})

    with pytest.raises(PaginationError) as error:
        fetch_paginated(provider, "daily", {"limit": 2}, page_size=2, max_pages=2)

    assert error.value.partial.status == "PARTIAL"
    assert error.value.partial.frame.shape[0] == 4
    assert "max_pages_reached" in error.value.partial.warnings
    assert [call["offset"] for call in provider.calls] == [0, 2]


def test_repeated_page_is_partial_instead_of_silent_success() -> None:
    repeated = _page(1, 2)
    provider = PageProvider({0: repeated, 2: repeated})

    with pytest.raises(PaginationError) as error:
        fetch_paginated(provider, "daily", {"limit": 2}, page_size=2, max_pages=3)

    assert "duplicate_page" in error.value.partial.warnings
    assert len(error.value.partial.frame) == 2


def test_schema_drift_is_recorded_while_unioning_page_columns() -> None:
    provider = PageProvider({0: _page(1, 2), 2: _page(3, 1, extra=True)})

    result = fetch_paginated_audited(
        provider, "daily", {"limit": 2}, page_size=2, max_pages=3
    )

    assert result.status == "PASS"
    assert len(result.schema_hashes) == 2
    assert "schema_drift" in result.warnings
    assert result.frame.columns.tolist() == ["ts_code", "trade_date", "close", "volume"]
    assert pd.isna(result.frame.iloc[0]["volume"])


def test_provider_failure_on_later_page_is_not_marked_complete() -> None:
    provider = PageProvider(
        {
            0: _page(1, 2),
            2: _page(3, 2),
            4: ProviderError("daily", "connection", "page 3 failed", attempts=1),
        }
    )

    with pytest.raises(ProviderError):
        fetch_paginated(provider, "daily", {"limit": 2}, page_size=2, max_pages=3)

    assert [call["offset"] for call in provider.calls] == [0, 2, 4]
