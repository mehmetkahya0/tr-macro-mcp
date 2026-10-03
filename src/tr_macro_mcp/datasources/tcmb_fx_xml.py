"""Keyless TCMB indicative exchange rates from the daily XML bulletins.

Verified behaviour (docs/data-sources.md §1): one bulletin per business day around 15:30
Istanbul time; HTTP 404 when no bulletin exists; rates are per <Unit> units; bulletins before
2005 are in old lira (TRL), 1 TRY = 1,000,000 TRL.
"""

import datetime as dt
from collections.abc import Callable
from zoneinfo import ZoneInfo

from defusedxml import ElementTree
from pydantic import BaseModel

from tr_macro_mcp.errors import InvalidRequestError, UpstreamError
from tr_macro_mcp.httpclient import PoliteClient
from tr_macro_mcp.models import (
    Aggregation,
    Citation,
    Frequency,
    FxQuote,
    FxRateResult,
    FxRateType,
    Observation,
    SeriesResult,
)
from tr_macro_mcp.store.timeseries import CachedBulletin, Store, utcnow

SOURCE_LABEL = "TCMB indicative exchange rates (daily XML bulletin)"
BASE_URL = "https://www.tcmb.gov.tr/kurlar"
TODAY_URL = f"{BASE_URL}/today.xml"
ISTANBUL = ZoneInfo("Europe/Istanbul")
REDENOMINATION_DATE = dt.date(2005, 1, 1)
TRL_PER_TRY = 1_000_000
EARLIEST_SUPPORTED = dt.date(1996, 1, 1)
LATEST_TTL = dt.timedelta(minutes=15)
NEAREST_LOOKBACK_DAYS = 14
RATE_FIELDS: dict[FxRateType, str] = {
    "forex_buying": "ForexBuying",
    "forex_selling": "ForexSelling",
    "banknote_buying": "BanknoteBuying",
    "banknote_selling": "BanknoteSelling",
}
MAX_RANGE_DAYS: dict[str, int] = {"daily": 62, "weekly": 366, "monthly": 5 * 366}


def bulletin_url(day: dt.date) -> str:
    return f"{BASE_URL}/{day:%Y%m}/{day:%d%m%Y}.xml"


def istanbul_today() -> dt.date:
    return dt.datetime.now(ISTANBUL).date()


class ParsedBulletin(BaseModel):
    bulletin_date: dt.date
    bulletin_no: str | None
    redenominated_from_trl: bool
    quotes: dict[str, FxQuote]


def _rate(text: str | None, unit: int, divisor: int) -> float | None:
    if text is None or not text.strip():
        return None
    return float(text.strip()) / unit / divisor


def parse_bulletin(content: bytes) -> ParsedBulletin:
    """Parse one XML bulletin. Rates are normalised to TRY per 1 unit of currency."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise UpstreamError(f"TCMB bulletin is not valid XML: {exc}") from exc
    tarih = root.get("Tarih")
    if root.tag != "Tarih_Date" or not tarih:
        raise UpstreamError("TCMB bulletin has an unexpected structure (no Tarih_Date/Tarih).")
    day = dt.datetime.strptime(tarih, "%d.%m.%Y").date()
    divisor = TRL_PER_TRY if day < REDENOMINATION_DATE else 1
    quotes: dict[str, FxQuote] = {}
    for node in root.findall("Currency"):
        code = (node.get("CurrencyCode") or node.get("Kod") or "").strip().upper()
        if not code:
            continue
        unit = int((node.findtext("Unit") or "1").strip() or "1")
        quotes[code] = FxQuote(
            currency=code,
            name=(node.findtext("CurrencyName") or node.findtext("Isim") or code).strip(),
            unit=unit,
            **{
                field: _rate(node.findtext(tag), unit, divisor)
                for field, tag in RATE_FIELDS.items()
            },
        )
    return ParsedBulletin(
        bulletin_date=day,
        bulletin_no=root.get("Bulten_No"),
        redenominated_from_trl=divisor != 1,
        quotes=quotes,
    )


class BulletinLookup(BaseModel):
    day: dt.date
    url: str
    bulletin: ParsedBulletin | None
    retrieved_at: dt.datetime


def parse_fx_code(series_code: str) -> tuple[str, FxRateType]:
    """'FX:USD' or 'FX:USD:forex_selling' -> ('USD', rate_type)."""
    parts = series_code.split(":")
    if len(parts) not in (2, 3) or parts[0].upper() != "FX" or len(parts[1]) != 3:
        raise InvalidRequestError(
            f"Invalid FX series code {series_code!r}. Use 'FX:<CUR>' or 'FX:<CUR>:<rate_type>' "
            f"with rate_type in {sorted(RATE_FIELDS)}."
        )
    rate_type = parts[2] if len(parts) == 3 else "forex_buying"
    if rate_type not in RATE_FIELDS:
        raise InvalidRequestError(
            f"Unknown rate_type {rate_type!r}; use one of {sorted(RATE_FIELDS)}."
        )
    return parts[1].upper(), rate_type


class TcmbXmlFxSource:
    name = "tcmb_fx_xml"
    description = (
        "TCMB indicative exchange rates from the daily XML bulletins (forex/banknote buying and "
        "selling, TRY per unit). Keyless. Series codes: 'FX:<CUR>[:<rate_type>]'."
    )
    coverage = (
        "About 20 currencies (USD, EUR, GBP, CHF, JPY, ...) plus XDR; business days since 1996; "
        "one bulletin per business day, published around 15:30 Istanbul time."
    )
    requires_key = False

    def __init__(
        self, http: PoliteClient, store: Store, today: Callable[[], dt.date] = istanbul_today
    ) -> None:
        self._http = http
        self._store = store
        self._today = today

    def available(self) -> bool:
        return True

    def handles(self, series_code: str) -> bool:
        return series_code.upper().startswith("FX:")

    async def fetch_bulletin(self, day: dt.date) -> BulletinLookup:
        """Bulletin for exactly `day`, or bulletin=None if TCMB published none (HTTP 404)."""
        url = bulletin_url(day)
        cached = self._store.get_bulletin(day)
        if cached is not None and (cached.status == "ok" or day < self._today()):
            bulletin = (
                ParsedBulletin.model_validate_json(cached.payload) if cached.payload else None
            )
            return BulletinLookup(
                day=day, url=url, bulletin=bulletin, retrieved_at=cached.retrieved_at
            )
        response = await self._http.get(url)
        retrieved_at = utcnow()
        if response.status_code == 404:
            if day < self._today():  # past non-publication days never change
                self._store.put_bulletin(
                    CachedBulletin(
                        bulletin_date=day,
                        status="not_published",
                        bulletin_no=None,
                        url=url,
                        payload=None,
                        retrieved_at=retrieved_at,
                    )
                )
            return BulletinLookup(day=day, url=url, bulletin=None, retrieved_at=retrieved_at)
        if response.status_code != 200:
            raise UpstreamError(f"TCMB returned HTTP {response.status_code} for {url}")
        bulletin = parse_bulletin(response.content)
        if bulletin.bulletin_date != day:
            raise UpstreamError(
                f"TCMB file {url} is dated {bulletin.bulletin_date}, not {day}; refusing to use it."
            )
        self._cache(bulletin, url, retrieved_at)
        return BulletinLookup(day=day, url=url, bulletin=bulletin, retrieved_at=retrieved_at)

    async def fetch_latest(self) -> BulletinLookup:
        """The most recent bulletin (today.xml), whatever its date."""
        cached = self._store.get_response(TODAY_URL, LATEST_TTL)
        if cached is not None:
            body, retrieved_at = cached.body, cached.retrieved_at
        else:
            response = await self._http.get(TODAY_URL)
            if response.status_code != 200:
                raise UpstreamError(f"TCMB returned HTTP {response.status_code} for {TODAY_URL}")
            body, retrieved_at = response.content, utcnow()
            self._store.put_response(TODAY_URL, body, retrieved_at)
        bulletin = parse_bulletin(body)
        url = bulletin_url(bulletin.bulletin_date)
        self._cache(bulletin, url, retrieved_at)
        return BulletinLookup(
            day=bulletin.bulletin_date, url=url, bulletin=bulletin, retrieved_at=retrieved_at
        )

    def _cache(self, bulletin: ParsedBulletin, url: str, retrieved_at: dt.datetime) -> None:
        self._store.put_bulletin(
            CachedBulletin(
                bulletin_date=bulletin.bulletin_date,
                status="ok",
                bulletin_no=bulletin.bulletin_no,
                url=url,
                payload=bulletin.model_dump_json(),
                retrieved_at=retrieved_at,
            )
        )

    async def nearest_earlier(self, day: dt.date) -> dt.date | None:
        for offset in range(1, NEAREST_LOOKBACK_DAYS + 1):
            candidate = day - dt.timedelta(days=offset)
            if candidate < EARLIEST_SUPPORTED:
                return None
            if (await self.fetch_bulletin(candidate)).bulletin is not None:
                return candidate
        return None

    def _check_day(self, day: dt.date) -> None:
        if day > self._today():
            raise InvalidRequestError(f"{day} is in the future; TCMB has no bulletin for it yet.")
        if day < EARLIEST_SUPPORTED:
            raise InvalidRequestError("The TCMB XML archive starts in 1996.")

    async def get_rate(
        self, currency: str, day: dt.date | None, rate_type: FxRateType
    ) -> FxRateResult:
        currency = currency.strip().upper()
        if day is not None:
            self._check_day(day)
        lookup = await (self.fetch_latest() if day is None else self.fetch_bulletin(day))
        citation = Citation(
            source=SOURCE_LABEL,
            title="Indicative exchange rates bulletin",
            url=lookup.url,
            doc_date=lookup.bulletin.bulletin_date if lookup.bulletin else None,
            bulletin_no=lookup.bulletin.bulletin_no if lookup.bulletin else None,
            retrieved_at=lookup.retrieved_at,
        )
        if lookup.bulletin is None:
            if day is None:
                raise UpstreamError("TCMB today.xml contained no bulletin.")
            nearest = await self.nearest_earlier(day)
            return FxRateResult(
                status="not_published",
                requested_date=day,
                bulletin_date=None,
                rate_type=rate_type,
                value=None,
                nearest_earlier_bulletin=nearest,
                citations=[citation],
                notes=[
                    f"TCMB published no exchange-rate bulletin for {day} ({day:%A}): weekend, "
                    "public holiday, or not yet published (bulletins appear around 15:30 "
                    "Istanbul time).",
                    *(
                        [
                            f"The closest earlier bulletin is {nearest}; request that date "
                            "explicitly if its rates are acceptable."
                        ]
                        if nearest
                        else []
                    ),
                ],
            )
        bulletin = lookup.bulletin
        notes = []
        if day is None:
            notes.append(f"Latest bulletin is dated {bulletin.bulletin_date}.")
        quote = bulletin.quotes.get(currency)
        if quote is None:
            return FxRateResult(
                status="unknown_currency",
                requested_date=day,
                bulletin_date=bulletin.bulletin_date,
                bulletin_no=bulletin.bulletin_no,
                rate_type=rate_type,
                value=None,
                available_currencies=sorted(bulletin.quotes),
                citations=[citation],
                notes=[*notes, f"{currency} is not in the {bulletin.bulletin_date} bulletin."],
            )
        if quote.unit != 1:
            notes.append(
                f"TCMB quotes {currency} per {quote.unit} units; values here are per 1 unit."
            )
        if bulletin.redenominated_from_trl:
            notes.append(
                "Pre-2005 bulletin published in old lira (TRL); values converted to TRY at "
                "1 TRY = 1,000,000 TRL."
            )
        value = getattr(quote, rate_type)
        if value is None:
            notes.append(f"TCMB published no {rate_type} rate for {currency} in this bulletin.")
        return FxRateResult(
            status="ok",
            requested_date=day,
            bulletin_date=bulletin.bulletin_date,
            bulletin_no=bulletin.bulletin_no,
            quote=quote,
            rate_type=rate_type,
            value=value,
            citations=[citation],
            notes=notes,
        )

    async def get_series(
        self,
        series_code: str,
        start: dt.date,
        end: dt.date,
        frequency: Frequency | None = None,
        aggregation: Aggregation | None = None,
    ) -> SeriesResult:
        currency, rate_type = parse_fx_code(series_code)
        freq = "daily" if frequency in (None, "daily", "business") else frequency
        if freq not in MAX_RANGE_DAYS:
            raise InvalidRequestError(
                f"Keyless FX series support daily, weekly or monthly frequency, not {frequency!r}."
            )
        if freq != "daily" and aggregation not in (None, "last"):
            raise InvalidRequestError(
                "The TCMB XML bulletins have no server-side aggregation; keyless weekly/monthly "
                "series use the last published bulletin of each period (aggregation='last'). "
                "For averages use EVDS (requires EVDS_API_KEY)."
            )
        notes: list[str] = []
        today = self._today()
        if end > today:
            notes.append(f"End date clamped from {end} to today ({today}).")
            end = today
        if start > end:
            raise InvalidRequestError(f"start ({start}) is after end ({end}).")
        start = max(start, EARLIEST_SUPPORTED)
        if (end - start).days > MAX_RANGE_DAYS[freq]:
            raise InvalidRequestError(
                f"Keyless {freq} FX series are limited to {MAX_RANGE_DAYS[freq]} days per call "
                "(one HTTP request per bulletin, rate-limited). Narrow the range, use a coarser "
                "frequency, or use EVDS with a key for long histories."
            )
        if freq == "daily":
            observations, retrieved = await self._daily(currency, rate_type, start, end, notes)
        else:
            observations, retrieved = await self._period_ends(
                currency, rate_type, start, end, freq, notes
            )
        return SeriesResult(
            series_code=f"FX:{currency}:{rate_type}",
            source=self.name,
            name=f"{currency}/TRY {rate_type.replace('_', ' ')}",
            unit=f"TRY per 1 {currency}",
            frequency=freq,
            aggregation=None if freq == "daily" else "last",
            start=start,
            end=end,
            observations=observations,
            as_of=max(retrieved, default=utcnow()),
            citations=[
                Citation(
                    source=SOURCE_LABEL,
                    title=f"Daily bulletins {start} to {end}",
                    url=f"{BASE_URL}/YYYYMM/DDMMYYYY.xml (one file per observation date)",
                    retrieved_at=max(retrieved, default=utcnow()),
                    series_code=f"FX:{currency}:{rate_type}",
                )
            ],
            notes=notes,
        )

    async def _daily(
        self,
        currency: str,
        rate_type: FxRateType,
        start: dt.date,
        end: dt.date,
        notes: list[str],
    ) -> tuple[list[Observation], list[dt.datetime]]:
        observations: list[Observation] = []
        retrieved: list[dt.datetime] = []
        missing: list[dt.date] = []
        day = start
        while day <= end:
            if day.weekday() < 5:
                lookup = await self.fetch_bulletin(day)
                retrieved.append(lookup.retrieved_at)
                if lookup.bulletin is None:
                    missing.append(day)
                else:
                    observations.append(self._observation(lookup.bulletin, currency, rate_type))
            day += dt.timedelta(days=1)
        notes.append("Weekends are not queried; TCMB does not publish bulletins on weekends.")
        if missing:
            notes.append(
                "No bulletin published on these weekdays (holiday or not yet published): "
                + ", ".join(d.isoformat() for d in missing)
            )
        if any(o.date and o.date < REDENOMINATION_DATE for o in observations):
            notes.append("Pre-2005 values converted from old lira at 1 TRY = 1,000,000 TRL.")
        return observations, retrieved

    async def _period_ends(
        self,
        currency: str,
        rate_type: FxRateType,
        start: dt.date,
        end: dt.date,
        freq: str,
        notes: list[str],
    ) -> tuple[list[Observation], list[dt.datetime]]:
        observations: list[Observation] = []
        retrieved: list[dt.datetime] = []
        for period_start, period_end in _periods(start, end, freq):
            day = period_end
            while day >= period_start:
                lookup = await self.fetch_bulletin(day) if day.weekday() < 5 else None
                if lookup is not None:
                    retrieved.append(lookup.retrieved_at)
                    if lookup.bulletin is not None:
                        obs = self._observation(lookup.bulletin, currency, rate_type)
                        label = (
                            f"{period_start:%Y-%m}"
                            if freq == "monthly"
                            else f"{period_start:%G-W%V}"
                        )
                        observations.append(
                            obs.model_copy(update={"period": f"{label} (bulletin {obs.period})"})
                        )
                        break
                day -= dt.timedelta(days=1)
        notes.append(
            f"Each {freq} value is the last published bulletin within the period "
            "(the bulletin date is shown in 'period')."
        )
        return observations, retrieved

    @staticmethod
    def _observation(bulletin: ParsedBulletin, currency: str, rate_type: FxRateType) -> Observation:
        quote = bulletin.quotes.get(currency)
        return Observation(
            period=bulletin.bulletin_date.isoformat(),
            date=bulletin.bulletin_date,
            value=getattr(quote, rate_type) if quote else None,
        )


def _periods(start: dt.date, end: dt.date, freq: str) -> list[tuple[dt.date, dt.date]]:
    """Calendar periods (clipped to [start, end]) for weekly or monthly sampling."""
    periods: list[tuple[dt.date, dt.date]] = []
    cursor = start
    while cursor <= end:
        if freq == "weekly":
            period_end = cursor + dt.timedelta(days=6 - cursor.weekday())
        else:
            next_month = (cursor.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
            period_end = next_month - dt.timedelta(days=1)
        periods.append((cursor, min(period_end, end)))
        cursor = period_end + dt.timedelta(days=1)
    return periods
