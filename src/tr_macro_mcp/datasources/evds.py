"""TCMB EVDS web service (requires a free EVDS_API_KEY).

Verified (docs/data-sources.md §2): base URL, `key` request header, parameters in the path
joined by '&', dd-mm-yyyy dates, max 1000 observations per request, no search endpoint.

NOT yet verified against a live keyed response: the JSON field names. The parser follows
the `evds` client TCMB's Python guide points to ({"items": [{"Tarih": ..., "TP_X_Y": "1.23"}]},
series code dots replaced by underscores) and fails loudly if the shape differs. Re-record
fixtures with `scripts/record_evds_fixtures.py` once a key is available.
"""

import datetime as dt
import json
import re
from typing import Any

from pydantic import SecretStr

from tr_macro_mcp.errors import InvalidRequestError, MissingKeyError, UpstreamError
from tr_macro_mcp.httpclient import PoliteClient
from tr_macro_mcp.models import (
    Aggregation,
    Citation,
    Frequency,
    Observation,
    SeriesResult,
    SeriesSearchHit,
    SeriesSearchResult,
)
from tr_macro_mcp.store.timeseries import Store, utcnow
from tr_macro_mcp.text import fold

BASE_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis/"
WEB_URL = "https://evds3.tcmb.gov.tr/"
SOURCE_LABEL = "TCMB EVDS (Electronic Data Delivery System)"
MAX_OBSERVATIONS = 1000
MAX_CHUNKS = 50
DATA_TTL = dt.timedelta(hours=24)
METADATA_TTL = dt.timedelta(days=7)
SEARCH_DATAGROUPS = 6
FREQUENCY_CODES: dict[str, int] = {
    "daily": 1,
    "business": 2,
    "weekly": 3,
    "biweekly": 4,
    "monthly": 5,
    "quarterly": 6,
    "semiannual": 7,
    "annual": 8,
}
_SERIES_CODE = re.compile(r"^[A-Za-z0-9_.]+$")


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", fold(text)) if t]


def _field(row: dict[str, Any], name: str) -> Any:
    """Case-insensitive field access (the docs and live API differ in casing)."""
    lowered = name.lower()
    for key, value in row.items():
        if key.lower() == lowered:
            return value
    return None


def _text(row: dict[str, Any], name: str) -> str | None:
    value = _field(row, name)
    return str(value).strip() if value not in (None, "") else None


def parse_period(label: str) -> dt.date | None:
    """Start date of an EVDS period label; None if the format is not recognised."""
    label = label.strip()
    patterns: list[tuple[str, Any]] = [
        (r"^(\d{2})-(\d{2})-(\d{4})$", lambda m: dt.date(int(m[3]), int(m[2]), int(m[1]))),
        (r"^(\d{4})-(\d{1,2})$", lambda m: dt.date(int(m[1]), int(m[2]), 1)),
        (r"^(\d{4})-Q([1-4])$", lambda m: dt.date(int(m[1]), 3 * int(m[2]) - 2, 1)),
        (r"^(\d{4})-[SH]([12])$", lambda m: dt.date(int(m[1]), 6 * int(m[2]) - 5, 1)),
        (r"^(\d{4})$", lambda m: dt.date(int(m[1]), 1, 1)),
    ]
    for pattern, build in patterns:
        match = re.match(pattern, label, flags=re.IGNORECASE)
        if match:
            try:
                return build(match)  # type: ignore[no-any-return]
            except ValueError:
                return None
    return None


def _value(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in {"null", "nan", "nd"}:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise UpstreamError(f"EVDS returned a non-numeric value {text!r}.") from exc


def parse_observations(payload: Any, series_code: str) -> list[Observation]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise UpstreamError("Unexpected EVDS response: no 'items' list.")
    column = series_code.replace(".", "_")
    observations: list[Observation] = []
    for item in payload["items"]:
        period = _text(item, "Tarih")
        if period is None:
            raise UpstreamError(f"Unexpected EVDS item without 'Tarih': keys={sorted(item)}")
        if column not in item and len(item) > 2:
            raise UpstreamError(
                f"EVDS item has no column {column!r}; keys={sorted(item)}. "
                "Was the series code typed exactly?"
            )
        observations.append(
            Observation(period=period, date=parse_period(period), value=_value(item.get(column)))
        )
    return observations


class EvdsSource:
    name = "evds"
    description = (
        "TCMB EVDS: tens of thousands of official series (policy and market rates, inflation, "
        "reserves, balance of payments, surveys, FX). Requires a free EVDS_API_KEY."
    )
    coverage = (
        "Turkish macro-financial series at daily to annual frequency; history varies by series."
    )
    requires_key = True

    def __init__(self, http: PoliteClient, store: Store, api_key: SecretStr | None) -> None:
        self._http = http
        self._store = store
        self._key = api_key.get_secret_value() if api_key else ""

    def available(self) -> bool:
        return bool(self._key)

    def handles(self, series_code: str) -> bool:
        return not series_code.upper().startswith("FX:")

    async def _get_json(self, path: str, ttl: dt.timedelta) -> tuple[Any, dt.datetime]:
        if not self._key:
            raise MissingKeyError()
        url = BASE_URL + path
        cached = self._store.get_response(url, ttl)  # cache key never contains the API key
        if cached is not None:
            return json.loads(cached.body), cached.retrieved_at
        response = await self._http.get(url, headers={"key": self._key})
        if response.status_code in (401, 403):
            raise UpstreamError(
                f"EVDS rejected the request (HTTP {response.status_code}: "
                f"{response.text[:120]!r}). Check that EVDS_API_KEY is correct."
            )
        if response.status_code != 200:
            raise UpstreamError(f"EVDS returned HTTP {response.status_code} for {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError(f"EVDS returned non-JSON content for {path}") from exc
        retrieved_at = utcnow()
        self._store.put_response(url, response.content, retrieved_at)
        return payload, retrieved_at

    async def series_metadata(self, series_code: str) -> dict[str, Any] | None:
        payload, _ = await self._get_json(f"serieList/type=json&code={series_code}", METADATA_TTL)
        rows = payload if isinstance(payload, list) else []
        for row in rows:
            if (_text(row, "SERIE_CODE") or "").upper() == series_code.upper():
                return dict(row)
        return None

    async def get_series(
        self,
        series_code: str,
        start: dt.date,
        end: dt.date,
        frequency: Frequency | None = None,
        aggregation: Aggregation | None = None,
    ) -> SeriesResult:
        if not self._key:
            raise MissingKeyError()
        if not _SERIES_CODE.match(series_code):
            raise InvalidRequestError(f"Invalid EVDS series code {series_code!r}.")
        if start > end:
            raise InvalidRequestError(f"start ({start}) is after end ({end}).")
        metadata = await self.series_metadata(series_code)
        if metadata is None:
            raise InvalidRequestError(
                f"EVDS has no series {series_code!r}. Use search_series to find the exact code."
            )
        observations, retrieved_at, notes = await self._fetch_range(
            series_code, start, end, frequency, aggregation
        )
        metadata_link = _text(metadata, "METADATA_LINK_ENG") or _text(metadata, "METADATA_LINK")
        if not observations:
            notes.append("EVDS returned no observations for this range.")
        return SeriesResult(
            series_code=series_code,
            source=self.name,
            name=_text(metadata, "SERIE_NAME"),
            name_en=_text(metadata, "SERIE_NAME_ENG"),
            unit=None,
            frequency=frequency or _text(metadata, "FREQUENCY_STR"),
            aggregation=aggregation or _text(metadata, "DEFAULT_AGG_METHOD_STR"),
            start=start,
            end=end,
            observations=observations,
            as_of=retrieved_at,
            citations=[
                Citation(
                    source=SOURCE_LABEL,
                    title=_text(metadata, "SERIE_NAME_ENG") or _text(metadata, "SERIE_NAME"),
                    url=metadata_link or WEB_URL,
                    series_code=series_code,
                    retrieved_at=retrieved_at,
                )
            ],
            notes=[
                *notes,
                "EVDS publishes no separate unit field; the unit is usually stated in the "
                "series name.",
            ],
        )

    async def _fetch_range(
        self,
        series_code: str,
        start: dt.date,
        end: dt.date,
        frequency: Frequency | None,
        aggregation: Aggregation | None,
    ) -> tuple[list[Observation], dt.datetime, list[str]]:
        """Fetch [start, end], working backwards in chunks: EVDS returns at most the last
        1000 observations of a range, counted back from endDate."""
        collected: list[Observation] = []
        retrieved: list[dt.datetime] = []
        chunk_end = end
        for _ in range(MAX_CHUNKS):
            path = (
                f"series={series_code}&startDate={start:%d-%m-%Y}&endDate={chunk_end:%d-%m-%Y}"
                "&type=json"
            )
            if frequency:
                path += f"&frequency={FREQUENCY_CODES[frequency]}"
            if aggregation:
                path += f"&aggregationTypes={aggregation}"
            payload, retrieved_at = await self._get_json(path, DATA_TTL)
            chunk = parse_observations(payload, series_code)
            retrieved.append(retrieved_at)
            collected = chunk + collected
            earliest = min((o.date for o in chunk if o.date), default=None)
            if len(chunk) < MAX_OBSERVATIONS or earliest is None or earliest <= start:
                return collected, min(retrieved), []
            chunk_end = earliest - dt.timedelta(days=1)
        return (
            collected,
            min(retrieved),
            [f"Stopped after {MAX_CHUNKS} requests; the earliest part of the range is missing."],
        )

    async def search(self, query: str, limit: int) -> SeriesSearchResult:
        if not self._key:
            raise MissingKeyError()
        query_tokens = _tokens(query)
        if not query_tokens:
            raise InvalidRequestError("Search query is empty.")
        groups, catalog_as_of = await self._get_json("datagroups/mode=0&type=json", METADATA_TTL)
        groups = groups if isinstance(groups, list) else []
        group_names = {
            (_text(g, "DATAGROUP_CODE") or ""): _text(g, "DATAGROUP_NAME_ENG")
            or _text(g, "DATAGROUP_NAME")
            for g in groups
        }
        ranked_groups = sorted(
            (
                (
                    _score(
                        query_tokens,
                        " ".join(
                            filter(
                                None,
                                [
                                    _text(g, "DATAGROUP_NAME"),
                                    _text(g, "DATAGROUP_NAME_ENG"),
                                    _text(g, "DATAGROUP_CODE"),
                                ],
                            )
                        ),
                    ),
                    _text(g, "DATAGROUP_CODE"),
                )
                for g in groups
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        candidates = [code for score, code in ranked_groups if score > 0 and code]
        candidates = candidates[:SEARCH_DATAGROUPS]
        if re.match(r"^TP\.", query.strip(), flags=re.IGNORECASE):
            candidates.insert(0, query.strip())  # serieList also accepts a series code
        hits: dict[str, SeriesSearchHit] = {}
        for rank, code in enumerate(candidates):
            rows, _ = await self._get_json(f"serieList/type=json&code={code}", METADATA_TTL)
            for row in rows if isinstance(rows, list) else []:
                hit = self._hit(row, query_tokens, group_names, group_bonus=1.0 / (rank + 2))
                if hit and (hit.series_code not in hits or hits[hit.series_code].score < hit.score):
                    hits[hit.series_code] = hit
        ordered = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:limit]
        return SeriesSearchResult(
            query=query,
            hits=ordered,
            catalog_as_of=catalog_as_of,
            citations=[Citation(source=SOURCE_LABEL, url=WEB_URL, retrieved_at=catalog_as_of)],
            notes=(
                []
                if ordered
                else [
                    "No matching series. Try other words (Turkish or English), a broader "
                    "topic (e.g. 'repo', 'TÜFE', 'rezerv'), or an exact code starting with 'TP.'."
                ]
            ),
        )

    @staticmethod
    def _hit(
        row: dict[str, Any],
        query_tokens: list[str],
        group_names: dict[str, str | None],
        group_bonus: float,
    ) -> SeriesSearchHit | None:
        code = _text(row, "SERIE_CODE")
        name = _text(row, "SERIE_NAME")
        if not code or not name:
            return None
        haystack = " ".join(
            filter(
                None,
                [
                    code,
                    name,
                    _text(row, "SERIE_NAME_ENG"),
                    _text(row, "TAG"),
                    _text(row, "TAG_ENG"),
                ],
            )
        )
        score = _score(query_tokens, haystack)
        if score == 0:
            return None
        group = _text(row, "DATAGROUP_CODE") or ""
        return SeriesSearchHit(
            series_code=code,
            name=name,
            name_en=_text(row, "SERIE_NAME_ENG"),
            datagroup_code=group,
            datagroup_name=group_names.get(group),
            frequency=_text(row, "FREQUENCY_STR"),
            start=_text(row, "START_DATE"),
            end=_text(row, "END_DATE"),
            score=round(score + group_bonus, 3),
        )


def _matches(query_token: str, word: str) -> bool:
    """Prefix match in both directions (min. 4 chars) to absorb Turkish suffixes:
    'faizi' ~ 'faiz', 'enflasyon' ~ 'enflasyonu'."""
    if word.startswith(query_token):
        return True
    return len(word) >= 4 and query_token.startswith(word)


def _score(query_tokens: list[str], text: str) -> float:
    """Share of query tokens found in text."""
    words = _tokens(text)
    if not words:
        return 0.0
    found = sum(1 for q in query_tokens if any(_matches(q, w) for w in words))
    return found / len(query_tokens)
