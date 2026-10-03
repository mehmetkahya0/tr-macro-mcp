"""EVDS tests. Observation/catalog payloads are SYNTHETIC until a key is available to record
real ones (scripts/record_evds_fixtures.py); test_recorded_fixtures_parse covers those."""

import datetime as dt
import json
import tempfile
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from tests.conftest import FIXTURES, TEST_KEY, FakeEvds
from tr_macro_mcp.datasources.evds import (
    EvdsSource,
    fold,
    parse_observations,
    parse_period,
)
from tr_macro_mcp.errors import InvalidRequestError, MissingKeyError, UpstreamError
from tr_macro_mcp.httpclient import PoliteClient
from tr_macro_mcp.store.timeseries import Store

pytestmark = pytest.mark.anyio
RECORDED = FIXTURES / "evds" / "recorded"


@pytest.fixture
def fake() -> FakeEvds:
    return FakeEvds()


def make_source(fake: FakeEvds, key: str | None = TEST_KEY) -> EvdsSource:
    store = Store(Path(tempfile.mkdtemp()) / "cache.duckdb")
    http = PoliteClient(min_interval=0, timeout=5, transport=httpx.MockTransport(fake))
    return EvdsSource(http, store, SecretStr(key) if key else None)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("02-01-2025", dt.date(2025, 1, 2)),
        ("2025-1", dt.date(2025, 1, 1)),
        ("2025-11", dt.date(2025, 11, 1)),
        ("2025-Q3", dt.date(2025, 7, 1)),
        ("2025-S2", dt.date(2025, 7, 1)),
        ("2025", dt.date(2025, 1, 1)),
        ("31-02-2025", None),
        ("week 3", None),
    ],
)
def test_parse_period(label: str, expected: dt.date | None) -> None:
    assert parse_period(label) == expected


def test_fold_is_turkish_aware() -> None:
    assert fold("DÖVİZ Kuru ışık") == "doviz kuru isik"


def test_parse_observations_handles_missing_values() -> None:
    payload = {
        "items": [
            {"Tarih": "01-01-2025", "TP_A_B": "1.5", "UNIXTIME": 0},
            {"Tarih": "02-01-2025", "TP_A_B": None, "UNIXTIME": 0},
            {"Tarih": "03-01-2025", "TP_A_B": "", "UNIXTIME": 0},
        ]
    }
    values = [o.value for o in parse_observations(payload, "TP.A.B")]
    assert values == [1.5, None, None]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": []},
        {"items": [{"TP_A_B": "1"}]},
        {"items": [{"Tarih": "01-01-2025", "TP_OTHER": "1", "UNIXTIME": 0}]},
        {"items": [{"Tarih": "01-01-2025", "TP_A_B": "abc", "UNIXTIME": 0}]},
    ],
)
def test_parse_observations_fails_loudly_on_unexpected_shape(payload: object) -> None:
    with pytest.raises(UpstreamError):
        parse_observations(payload, "TP.A.B")


async def test_without_key_every_call_explains_how_to_fix(fake: FakeEvds) -> None:
    source = make_source(fake, key=None)
    assert not source.available()
    with pytest.raises(MissingKeyError, match="EVDS_API_KEY not set; see README"):
        await source.get_series("TP.TEST.RATE", dt.date(2025, 1, 1), dt.date(2025, 1, 5))
    with pytest.raises(MissingKeyError):
        await source.search("faiz", 5)
    assert fake.requests == []


async def test_get_series_sends_key_header_and_cites(fake: FakeEvds) -> None:
    source = make_source(fake)
    result = await source.get_series("TP.TEST.RATE", dt.date(2025, 1, 1), dt.date(2025, 1, 3))
    assert [o.value for o in result.observations] == [1.5, 2.5, 3.5]
    assert result.name_en == "Test Policy Rate (synthetic, %)"
    assert result.citations[0].series_code == "TP.TEST.RATE"
    assert all(r.headers["key"] == TEST_KEY for r in fake.requests)
    assert all(TEST_KEY not in str(r.url) for r in fake.requests)


async def test_long_ranges_are_fetched_in_chunks(fake: FakeEvds) -> None:
    source = make_source(fake)
    start, end = dt.date(2020, 1, 1), dt.date(2025, 12, 31)
    result = await source.get_series("TP.TEST.RATE", start, end)
    dates = [o.date for o in result.observations]
    assert dates[0] == start
    assert dates[-1] == end
    assert len(dates) == len(set(dates)) == (end - start).days + 1
    assert sum(r.url.raw_path.decode().count("series=") for r in fake.requests) == 3


async def test_responses_are_cached(fake: FakeEvds) -> None:
    source = make_source(fake)
    for _ in range(2):
        await source.get_series("TP.TEST.RATE", dt.date(2025, 1, 1), dt.date(2025, 1, 3))
    assert len(fake.requests) == 2  # metadata + observations, once each


async def test_unknown_series_is_reported(fake: FakeEvds) -> None:
    fake.routes["serieList/type=json&code=TP.NOPE"] = []
    with pytest.raises(InvalidRequestError, match="search_series"):
        await make_source(fake).get_series("TP.NOPE", dt.date(2025, 1, 1), dt.date(2025, 1, 2))


async def test_bad_key_is_explained(fake: FakeEvds) -> None:
    with pytest.raises(UpstreamError, match="EVDS_API_KEY is correct"):
        await make_source(fake, key="wrong").search("faiz", 5)


@pytest.mark.parametrize("query", ["politika faizi", "POLİTİKA faiz", "policy rate"])
async def test_search_matches_turkish_and_english(fake: FakeEvds, query: str) -> None:
    result = await make_source(fake).search(query, 5)
    assert result.hits[0].series_code == "TP.TEST.RATE"
    assert result.hits[0].datagroup_name == "Test Interest Rates (synthetic)"


async def test_search_without_matches_says_so(fake: FakeEvds) -> None:
    result = await make_source(fake).search("zzzz", 5)
    assert result.hits == []
    assert result.notes


@pytest.mark.skipif(not RECORDED.exists(), reason="no recorded EVDS fixtures yet (needs a key)")
def test_recorded_fixtures_parse() -> None:
    for path in RECORDED.glob("series_*.json"):
        code = path.stem.removeprefix("series_").split("__")[0]
        observations = parse_observations(json.loads(path.read_text(encoding="utf-8")), code)
        assert observations
        assert all(o.date is not None for o in observations), path.name


@pytest.mark.skipif(not RECORDED.exists(), reason="no recorded EVDS fixtures yet (needs a key)")
def test_recorded_evds_matches_tcmb_xml_bulletin() -> None:
    """Cross-source check: EVDS USD buying rate on 2025-01-02 equals the XML bulletin."""
    payload = json.loads((RECORDED / "series_TP.DK.USD.A.YTL__daily.json").read_text("utf-8"))
    by_date = {o.date: o.value for o in parse_observations(payload, "TP.DK.USD.A.YTL")}
    assert by_date[dt.date(2025, 1, 2)] == 35.2525
