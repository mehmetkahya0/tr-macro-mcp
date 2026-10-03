import datetime as dt

import httpx
import pytest

from tests.conftest import FX_DIR, TODAY, FakeTcmb
from tr_macro_mcp.datasources.tcmb_fx_xml import TcmbXmlFxSource, parse_bulletin, parse_fx_code
from tr_macro_mcp.errors import InvalidRequestError, UpstreamError
from tr_macro_mcp.httpclient import PoliteClient
from tr_macro_mcp.store.timeseries import Store

pytestmark = pytest.mark.anyio


@pytest.fixture
def tcmb() -> FakeTcmb:
    return FakeTcmb()


@pytest.fixture
def source(tcmb: FakeTcmb) -> TcmbXmlFxSource:
    http = PoliteClient(min_interval=0, timeout=5, transport=httpx.MockTransport(tcmb))
    return TcmbXmlFxSource(http, Store(":memory:"), today=lambda: TODAY)


class TestParseBulletin:
    def test_current_bulletin(self) -> None:
        bulletin = parse_bulletin((FX_DIR / "today.xml").read_bytes())
        assert bulletin.bulletin_date == dt.date(2026, 10, 2)
        assert bulletin.bulletin_no == "2026/186"
        assert not bulletin.redenominated_from_trl
        usd = bulletin.quotes["USD"]
        assert (usd.forex_buying, usd.forex_selling) == (48.9699, 49.0582)
        assert usd.banknote_selling == 49.1318

    def test_unit_100_is_normalised_per_unit(self) -> None:
        jpy = parse_bulletin((FX_DIR / "today.xml").read_bytes()).quotes["JPY"]
        assert jpy.unit == 100
        assert jpy.forex_buying == pytest.approx(30.9865 / 100)

    def test_missing_banknote_rates_are_none_and_names_stripped(self) -> None:
        quotes = parse_bulletin((FX_DIR / "today.xml").read_bytes()).quotes
        assert quotes["CNY"].banknote_buying is None
        assert quotes["XDR"].name == "SPECIAL DRAWING RIGHT (SDR)"

    def test_pre_2005_trl_bulletin_is_converted(self) -> None:
        bulletin = parse_bulletin((FX_DIR / "02012004.xml").read_bytes())  # ISO-8859-9
        assert bulletin.redenominated_from_trl
        assert bulletin.quotes["USD"].forex_buying == pytest.approx(1.389282)

    @pytest.mark.parametrize("content", [b"not xml", b"<other/>", b"<Tarih_Date/>"])
    def test_malformed_input_raises(self, content: bytes) -> None:
        with pytest.raises(UpstreamError):
            parse_bulletin(content)


@pytest.mark.parametrize(
    ("code", "expected"),
    [("FX:usd", ("USD", "forex_buying")), ("FX:EUR:banknote_selling", ("EUR", "banknote_selling"))],
)
def test_parse_fx_code(code: str, expected: tuple[str, str]) -> None:
    assert parse_fx_code(code) == expected


@pytest.mark.parametrize("code", ["USD", "FX:", "FX:USDX", "FX:USD:mid"])
def test_parse_fx_code_rejects(code: str) -> None:
    with pytest.raises(InvalidRequestError):
        parse_fx_code(code)


class TestGetRate:
    async def test_business_day(self, source: TcmbXmlFxSource) -> None:
        result = await source.get_rate("usd", dt.date(2025, 1, 2), "forex_selling")
        assert result.status == "ok"
        assert result.bulletin_date == dt.date(2025, 1, 2)
        assert result.quote is not None
        assert result.value == result.quote.forex_selling == 35.316
        citation = result.citations[0]
        assert citation.url == "https://www.tcmb.gov.tr/kurlar/202501/02012025.xml"
        assert citation.bulletin_no == "2025/1"

    async def test_holiday_is_not_substituted(self, source: TcmbXmlFxSource) -> None:
        result = await source.get_rate("USD", dt.date(2025, 1, 1), "forex_buying")
        assert result.status == "not_published"
        assert result.value is None
        assert result.quote is None
        assert result.bulletin_date is None
        assert result.nearest_earlier_bulletin == dt.date(2024, 12, 31)

    async def test_weekend_points_to_friday(self, source: TcmbXmlFxSource) -> None:
        result = await source.get_rate("USD", dt.date(2025, 1, 4), "forex_buying")
        assert result.status == "not_published"
        assert result.nearest_earlier_bulletin == dt.date(2025, 1, 3)

    async def test_latest_reports_bulletin_date(self, source: TcmbXmlFxSource) -> None:
        result = await source.get_rate("EUR", None, "forex_buying")
        assert result.status == "ok"
        assert result.requested_date is None
        assert result.bulletin_date == dt.date(2026, 10, 2)
        assert any("2026-10-02" in note for note in result.notes)

    async def test_unknown_currency(self, source: TcmbXmlFxSource) -> None:
        result = await source.get_rate("XYZ", dt.date(2025, 1, 2), "forex_buying")
        assert result.status == "unknown_currency"
        assert "USD" in result.available_currencies

    async def test_future_date_rejected(self, source: TcmbXmlFxSource) -> None:
        with pytest.raises(InvalidRequestError, match="future"):
            await source.get_rate("USD", TODAY + dt.timedelta(days=1), "forex_buying")

    async def test_results_and_past_404s_are_cached(
        self, source: TcmbXmlFxSource, tcmb: FakeTcmb
    ) -> None:
        await source.get_rate("USD", dt.date(2025, 1, 1), "forex_buying")
        count = len(tcmb.requests)
        await source.get_rate("USD", dt.date(2025, 1, 1), "forex_buying")
        await source.get_rate("USD", dt.date(2024, 12, 31), "forex_buying")
        assert len(tcmb.requests) == count


class TestGetSeries:
    async def test_daily_skips_weekends_and_reports_holidays(self, source: TcmbXmlFxSource) -> None:
        result = await source.get_series("FX:USD", dt.date(2024, 12, 30), dt.date(2025, 1, 5))
        assert [o.period for o in result.observations] == [
            "2024-12-30",
            "2024-12-31",
            "2025-01-02",
            "2025-01-03",
        ]
        assert any("2025-01-01" in note for note in result.notes)
        assert result.unit == "TRY per 1 USD"

    async def test_monthly_uses_last_bulletin_of_month(self, source: TcmbXmlFxSource) -> None:
        result = await source.get_series(
            "FX:EUR", dt.date(2025, 1, 1), dt.date(2025, 3, 31), frequency="monthly"
        )
        assert [o.date for o in result.observations] == [
            dt.date(2025, 1, 31),
            dt.date(2025, 2, 28),
            dt.date(2025, 3, 28),  # 31 March was a holiday, 29-30 a weekend
        ]
        assert result.observations[0].period.startswith("2025-01")

    async def test_range_limit(self, source: TcmbXmlFxSource) -> None:
        with pytest.raises(InvalidRequestError, match="limited to 62 days"):
            await source.get_series("FX:USD", dt.date(2025, 1, 1), dt.date(2025, 6, 1))

    async def test_aggregation_other_than_last_rejected(self, source: TcmbXmlFxSource) -> None:
        with pytest.raises(InvalidRequestError, match="EVDS"):
            await source.get_series(
                "FX:USD", dt.date(2025, 1, 1), dt.date(2025, 3, 1), "monthly", "avg"
            )
