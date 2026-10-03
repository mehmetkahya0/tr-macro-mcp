import datetime as dt

import pytest

from tr_macro_mcp.models import Observation
from tr_macro_mcp.tools.series import period_stats


def obs(day: int, value: float | None) -> Observation:
    date = dt.date(2025, 1, day)
    return Observation(period=date.isoformat(), date=date, value=value)


def test_stats_ignore_nulls_and_out_of_range() -> None:
    stats = period_stats(
        [obs(3, 12.0), obs(1, 10.0), obs(2, None), obs(9, 99.0)],
        dt.date(2025, 1, 1),
        dt.date(2025, 1, 5),
    )
    assert (stats.count, stats.first, stats.last) == (2, 10.0, 12.0)
    assert (stats.mean, stats.min, stats.max) == (11.0, 10.0, 12.0)
    assert stats.change_abs == 2.0
    assert stats.change_pct == pytest.approx(20.0)


def test_empty_period_has_null_stats() -> None:
    stats = period_stats([], dt.date(2025, 1, 1), dt.date(2025, 1, 5))
    assert stats.count == 0
    assert stats.mean is None
    assert stats.change_pct is None
