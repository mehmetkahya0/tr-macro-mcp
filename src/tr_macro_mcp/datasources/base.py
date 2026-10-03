"""The one deliberate abstraction: a numeric data source with provenance."""

import datetime as dt
from typing import Protocol

from tr_macro_mcp.models import Aggregation, Frequency, SeriesResult


class DataSource(Protocol):
    name: str
    description: str
    coverage: str
    requires_key: bool

    def available(self) -> bool:
        """Whether the source can serve requests in this session (e.g. key present)."""
        ...

    def handles(self, series_code: str) -> bool:
        """Whether this source owns the given series code."""
        ...

    async def get_series(
        self,
        series_code: str,
        start: dt.date,
        end: dt.date,
        frequency: Frequency | None = None,
        aggregation: Aggregation | None = None,
    ) -> SeriesResult: ...
