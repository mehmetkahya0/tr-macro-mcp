"""Pydantic models shared by data sources and MCP tools."""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

Frequency = Literal[
    "daily", "business", "weekly", "biweekly", "monthly", "quarterly", "semiannual", "annual"
]
Aggregation = Literal["avg", "min", "max", "first", "last", "sum"]
FxRateType = Literal["forex_buying", "forex_selling", "banknote_buying", "banknote_selling"]


class Citation(BaseModel):
    """Where a value or excerpt came from. Every tool result carries at least one."""

    source: str = Field(description="Publisher and dataset, e.g. 'TCMB indicative exchange rates'.")
    title: str | None = None
    url: str
    doc_date: dt.date | None = Field(default=None, description="Publication/bulletin date.")
    page: int | None = Field(default=None, description="1-based PDF page.")
    page_label: str | None = Field(default=None, description="Printed page number, if different.")
    bulletin_no: str | None = None
    series_code: str | None = None
    retrieved_at: dt.datetime


class DataSourceInfo(BaseModel):
    name: str
    description: str
    coverage: str
    requires_key: bool
    available: bool
    unavailable_reason: str | None = None


class DataSourcesResult(BaseModel):
    sources: list[DataSourceInfo]
    keyless_mode: bool
    disclaimer: str


class Observation(BaseModel):
    period: str = Field(description="Period label exactly as published by the source.")
    date: dt.date | None = Field(description="Start date of the period, when it could be parsed.")
    value: float | None = Field(description="None when the source published no value.")


class SeriesResult(BaseModel):
    series_code: str
    source: str
    name: str | None = None
    name_en: str | None = None
    unit: str | None = None
    frequency: str | None = None
    aggregation: str | None = None
    start: dt.date
    end: dt.date
    observations: list[Observation]
    as_of: dt.datetime = Field(description="When the data was retrieved from the source.")
    citations: list[Citation]
    notes: list[str] = Field(default_factory=list)


class FxQuote(BaseModel):
    """Rates for one currency in one bulletin, per 1 unit of the currency, in TRY."""

    currency: str
    name: str
    unit: int = Field(description="Units the published rates referred to (e.g. 100 for JPY).")
    forex_buying: float | None
    forex_selling: float | None
    banknote_buying: float | None
    banknote_selling: float | None


class FxRateResult(BaseModel):
    status: Literal["ok", "not_published", "unknown_currency"]
    requested_date: dt.date | None = Field(description="None means 'latest bulletin'.")
    bulletin_date: dt.date | None = Field(
        description=(
            "Date of the bulletin the rates come from. Equals requested_date when status=ok."
        )
    )
    bulletin_no: str | None = None
    quote: FxQuote | None = None
    rate_type: FxRateType
    value: float | None = Field(description="The requested rate_type, per 1 unit, in TRY.")
    nearest_earlier_bulletin: dt.date | None = Field(
        default=None,
        description=(
            "Only for status=not_published: the closest earlier date that has a bulletin. "
            "Its rates are NOT the requested date's rates."
        ),
    )
    available_currencies: list[str] = Field(default_factory=list)
    citations: list[Citation]
    notes: list[str] = Field(default_factory=list)


class SeriesSearchHit(BaseModel):
    series_code: str
    name: str
    name_en: str | None
    datagroup_code: str
    datagroup_name: str | None
    frequency: str | None
    start: str | None
    end: str | None
    score: float


class SeriesSearchResult(BaseModel):
    query: str
    hits: list[SeriesSearchHit]
    catalog_as_of: dt.datetime | None
    citations: list[Citation]
    notes: list[str] = Field(default_factory=list)


class PeriodStats(BaseModel):
    start: dt.date
    end: dt.date
    count: int
    first: float | None
    last: float | None
    mean: float | None
    min: float | None
    max: float | None
    change_abs: float | None = Field(description="last - first within the period.")
    change_pct: float | None = Field(description="(last / first - 1) * 100 within the period.")


class PeriodComparison(BaseModel):
    series_code: str
    source: str
    name: str | None
    period_a: PeriodStats
    period_b: PeriodStats
    mean_change_abs: float | None = Field(description="mean(b) - mean(a).")
    mean_change_pct: float | None = Field(description="(mean(b) / mean(a) - 1) * 100.")
    last_change_abs: float | None = Field(description="last(b) - last(a).")
    last_change_pct: float | None = Field(description="(last(b) / last(a) - 1) * 100.")
    citations: list[Citation]
    notes: list[str] = Field(default_factory=list)
