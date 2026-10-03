import datetime as dt
from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from tr_macro_mcp.errors import InvalidRequestError
from tr_macro_mcp.models import (
    Aggregation,
    Frequency,
    Observation,
    PeriodComparison,
    PeriodStats,
    SeriesResult,
    SeriesSearchResult,
)
from tr_macro_mcp.tools import Services, tool_errors

SeriesCode = Annotated[
    str,
    Field(
        description=(
            "EVDS code such as 'TP.DK.USD.A.YTL' (requires EVDS_API_KEY; find codes with "
            "search_series) or keyless FX code 'FX:<CUR>[:<rate_type>]', e.g. 'FX:USD', "
            "'FX:EUR:forex_selling'."
        )
    ),
]


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new / old - 1) * 100, 6)


def _diff(new: float | None, old: float | None) -> float | None:
    return None if new is None or old is None else round(new - old, 10)


def period_stats(observations: list[Observation], start: dt.date, end: dt.date) -> PeriodStats:
    values = [
        o.value
        for o in sorted(observations, key=lambda o: o.date or dt.date.min)
        if o.value is not None and o.date is not None and start <= o.date <= end
    ]
    first = values[0] if values else None
    last = values[-1] if values else None
    return PeriodStats(
        start=start,
        end=end,
        count=len(values),
        first=first,
        last=last,
        mean=round(sum(values) / len(values), 10) if values else None,
        min=min(values) if values else None,
        max=max(values) if values else None,
        change_abs=_diff(last, first),
        change_pct=_pct(last, first),
    )


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @tool_errors
    async def search_series(
        query: Annotated[
            str,
            Field(description="Keywords in Turkish or English, e.g. 'politika faizi', 'CPI'."),
        ],
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> SeriesSearchResult:
        """Find EVDS series codes by keyword (Turkish or English). Requires EVDS_API_KEY.
        Returns codes, names, frequency and coverage dates; pass a code to get_series.
        Without a key, only keyless FX series ('FX:USD', ...) are available."""
        return await services.evds.search(query, limit)

    @mcp.tool()
    @tool_errors
    async def get_series(
        series_code: SeriesCode,
        start: Annotated[dt.date, Field(description="First date (YYYY-MM-DD), inclusive.")],
        end: Annotated[dt.date, Field(description="Last date (YYYY-MM-DD), inclusive.")],
        frequency: Annotated[
            Frequency | None,
            Field(description="Convert to this frequency. Omit for the series' own frequency."),
        ] = None,
        aggregation: Annotated[
            Aggregation | None,
            Field(description="How to aggregate when converting frequency."),
        ] = None,
        source: Annotated[
            str | None,
            Field(description="Force a source ('evds' or 'tcmb_fx_xml'); normally omitted."),
        ] = None,
    ) -> SeriesResult:
        """Observations of a time series with metadata (name, frequency, source) and citations.
        EVDS codes need EVDS_API_KEY. Keyless FX codes ('FX:USD') fetch one TCMB bulletin per
        day, so ranges are limited (daily: 62 days, weekly: 1 year, monthly: 5 years; weekly and
        monthly values are the last bulletin of each period). Observation values may be null
        where the source published nothing."""
        resolved = services.registry.resolve(series_code, source)
        return await resolved.get_series(series_code, start, end, frequency, aggregation)

    @mcp.tool()
    @tool_errors
    async def compare_periods(
        series_code: SeriesCode,
        period_a_start: dt.date,
        period_a_end: dt.date,
        period_b_start: dt.date,
        period_b_end: dt.date,
        frequency: Annotated[
            Frequency | None,
            Field(description="Optional frequency conversion before comparing."),
        ] = None,
    ) -> PeriodComparison:
        """Compare a series between two periods: count, first, last, mean, min, max and change
        within each period, plus the change in mean and in last value from period A to B.
        Purely descriptive statistics; no causal interpretation."""
        for start, end in ((period_a_start, period_a_end), (period_b_start, period_b_end)):
            if start > end:
                raise InvalidRequestError(f"Period start {start} is after its end {end}.")
        resolved = services.registry.resolve(series_code)
        result_a = await resolved.get_series(series_code, period_a_start, period_a_end, frequency)
        result_b = await resolved.get_series(series_code, period_b_start, period_b_end, frequency)
        stats_a = period_stats(result_a.observations, period_a_start, period_a_end)
        stats_b = period_stats(result_b.observations, period_b_start, period_b_end)
        notes = [*result_a.notes, *(n for n in result_b.notes if n not in result_a.notes)]
        if stats_a.count == 0 or stats_b.count == 0:
            notes.append("At least one period has no observations; some statistics are null.")
        return PeriodComparison(
            series_code=result_a.series_code,
            source=result_a.source,
            name=result_a.name_en or result_a.name,
            period_a=stats_a,
            period_b=stats_b,
            mean_change_abs=_diff(stats_b.mean, stats_a.mean),
            mean_change_pct=_pct(stats_b.mean, stats_a.mean),
            last_change_abs=_diff(stats_b.last, stats_a.last),
            last_change_pct=_pct(stats_b.last, stats_a.last),
            citations=[*result_a.citations, *result_b.citations],
            notes=notes,
        )
