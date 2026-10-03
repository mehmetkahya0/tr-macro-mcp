"""Discover TCMB documents by following links from index pages (never by constructing
detail URLs; their slugs are irregular, see docs/data-sources.md §3)."""

import datetime as dt
import re
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin

from pydantic import BaseModel

from tr_macro_mcp.errors import UpstreamError
from tr_macro_mcp.httpclient import PoliteClient
from tr_macro_mcp.text import fold

DocType = Literal["mpc", "ir", "fsr", "smp"]
Language = Literal["tr", "en"]
DatePrecision = Literal["day", "month"]

SITE = "https://www.tcmb.gov.tr"
WCM = f"{SITE}/wps/wcm/connect"
MPC_TR_INDEX = f"{WCM}/TR/TCMB+TR/PPK/PPK+Toplanti+Kararlari"
MPC_TR_PRESS = f"{WCM}/TR/TCMB+TR/Main+Menu/Duyurular/Basin/{{year}}"
MPC_EN_PRESS = f"{WCM}/EN/TCMB+EN/Main+Menu/Announcements/Press+Releases/{{year}}"
MPC_TITLE = {
    "tr": "Faiz Oranlarına İlişkin Basın Duyurusu",
    "en": "Press Release on Interest Rates",
}
REPORT_INDEX: dict[tuple[str, str], str] = {
    ("ir", "tr"): f"{WCM}/TR/TCMB+TR/Main+Menu/Yayinlar/Raporlar/Enflasyon+Raporu",
    ("ir", "en"): f"{WCM}/EN/TCMB+EN/Main+Menu/Publications/Reports/Inflation+Report",
    ("fsr", "tr"): f"{WCM}/TR/TCMB+TR/Main+Menu/Yayinlar/Raporlar/Finansal+Istikrar+Raporu",
    ("fsr", "en"): f"{WCM}/EN/TCMB+EN/Main+Menu/Publications/Reports/Financial+Stability+Report",
}
REPORT_TITLE = {
    ("ir", "tr"): "Enflasyon Raporu",
    ("ir", "en"): "Inflation Report",
    ("fsr", "tr"): "Finansal İstikrar Raporu",
    ("fsr", "en"): "Financial Stability Report",
}
FULL_TEXT_LINK = re.compile(r"click for full text|tam metin", re.IGNORECASE)
SMP_PAGE = {
    "tr": f"{WCM}/TR/TCMB+TR/Main+Menu/Istatistikler/Egilim+Anketleri/Piyasa+Katilimcilari+Anketi/",
    "en": f"{WCM}/EN/TCMB+EN/Main+Menu/Statistics/Tendency+Surveys/Survey+of+Market+Participants",
}
SMP_PDF = re.compile(r"/(PKA_Rapor|SMP_Report)\.pdf", re.IGNORECASE)

# Keys are Turkish-folded (see text.fold); dates are matched on folded text.
MONTHS = {
    **{
        fold(m): i
        for i, m in enumerate(
            [
                "Ocak",
                "Şubat",
                "Mart",
                "Nisan",
                "Mayıs",
                "Haziran",
                "Temmuz",
                "Ağustos",
                "Eylül",
                "Ekim",
                "Kasım",
                "Aralık",
            ],
            start=1,
        )
    },
    **{
        m: i
        for i, m in enumerate(
            [
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "july",
                "august",
                "september",
                "october",
                "november",
                "december",
            ],
            start=1,
        )
    },
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))


class Link(BaseModel):
    href: str
    text: str


class Candidate(BaseModel):
    doc_type: DocType
    language: Language
    title: str
    issue: str | None
    doc_date: dt.date | None
    date_precision: DatePrecision = "day"
    page_url: str
    pdf_url: str | None = None


class _LinkParser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__()
        self.base = base
        self.links: list[Link] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            href = self._href.strip()
            if href and not href.lower().startswith(("javascript:", "mailto:", "#")):
                text = " ".join("".join(self._text).split())
                self.links.append(Link(href=canonical_url(urljoin(self.base, href)), text=text))
            self._href = None


def canonical_url(url: str) -> str:
    """Drop WCM's volatile CACHEID parameter (verified unnecessary for PDF downloads)."""
    return re.sub(r"&CACHEID=[^&]*", "", url)


def extract_links(html: str, base: str) -> list[Link]:
    parser = _LinkParser(base)
    parser.feed(html)
    return parser.links


def parse_date(text: str) -> tuple[dt.date, DatePrecision] | None:
    """First date in text: '22 Ocak 2026', 'January 22, 2026', '21/01/2014', 'Mayıs 2026'."""
    lowered = fold(text)
    patterns: list[tuple[str, str]] = [
        (rf"\b(\d{{1,2}})\s+({_MONTH})\s+(\d{{4}})\b", "dmy"),
        (rf"\b({_MONTH})\s+(\d{{1,2}}),\s*(\d{{4}})\b", "mdy"),
        (r"\b(\d{2})/(\d{2})/(\d{4})\b", "numeric"),
        (rf"\b({_MONTH})\s+(\d{{4}})\b", "my"),
    ]
    for pattern, kind in patterns:
        m = re.search(pattern, lowered)
        if not m:
            continue
        try:
            if kind == "dmy":
                return dt.date(int(m[3]), MONTHS[m[2]], int(m[1])), "day"
            if kind == "mdy":
                return dt.date(int(m[3]), MONTHS[m[1]], int(m[2])), "day"
            if kind == "numeric":
                return dt.date(int(m[3]), int(m[2]), int(m[1])), "day"
            return dt.date(int(m[2]), MONTHS[m[1]], 1), "month"
        except ValueError:
            continue
    return None


class Discoverer:
    def __init__(self, http: PoliteClient, today: dt.date) -> None:
        self._http = http
        self._today = today

    async def _links(self, url: str, *, required: bool = True) -> list[Link]:
        response = await self._http.get(url)
        if response.status_code == 404 and not required:
            return []
        if response.status_code != 200:
            raise UpstreamError(f"TCMB returned HTTP {response.status_code} for {url}")
        return extract_links(response.text, SITE)

    async def mpc(self, language: Language, since_year: int) -> list[Candidate]:
        """MPC decisions. TR: the PPK index (lags) plus recent press-release lists;
        EN: yearly press-release lists. Numbering matches across languages."""
        if language == "tr":
            pages = [MPC_TR_INDEX] + [
                MPC_TR_PRESS.format(year=y) for y in (self._today.year - 1, self._today.year)
            ]
        else:
            pages = [MPC_EN_PRESS.format(year=y) for y in range(since_year, self._today.year + 1)]
        found: dict[str, Candidate] = {}
        code = "DUY" if language == "tr" else "ANO"
        for page in pages:
            for link in await self._links(page, required=page == MPC_TR_INDEX):
                issue = re.search(r"\((\d{4}-\d{2})\)", link.text)
                if not link.text.startswith(MPC_TITLE[language]) or not issue:
                    continue
                if f"/{code}{issue[1]}" not in link.href or int(issue[1][:4]) < since_year:
                    continue
                parsed = parse_date(link.text)
                found.setdefault(
                    issue[1],
                    Candidate(
                        doc_type="mpc",
                        language=language,
                        title=link.text,
                        issue=issue[1],
                        doc_date=parsed[0] if parsed else None,
                        page_url=link.href,
                    ),
                )
        return sorted(found.values(), key=lambda c: c.issue or "")

    async def mpc_pdf_url(self, candidate: Candidate) -> str:
        code = ("DUY" if candidate.language == "tr" else "ANO") + (candidate.issue or "")
        for link in await self._links(candidate.page_url):
            if f"/{code}.pdf" in link.href:
                return link.href
        raise UpstreamError(f"No PDF link for {code} on {candidate.page_url}")

    async def report(
        self, doc_type: DocType, language: Language, since_year: int
    ) -> list[Candidate]:
        index = REPORT_INDEX[(doc_type, language)]
        prefix = REPORT_TITLE[(doc_type, language)]
        found: dict[str, Candidate] = {}
        for link in await self._links(index):
            year = re.search(r"/(\d{4})/", link.href)
            if not link.text.startswith(prefix) or not year or int(year[1]) < since_year:
                continue
            if link.href in found or not link.href.startswith(index):
                continue
            parsed = parse_date(link.text)
            found[link.href] = Candidate(
                doc_type=doc_type,
                language=language,
                title=link.text,
                issue=_report_issue(doc_type, link.text),
                doc_date=parsed[0] if parsed else None,
                date_precision=parsed[1] if parsed else "day",
                page_url=link.href,
            )
        return sorted(found.values(), key=lambda c: c.doc_date or dt.date.min)

    async def report_pdf_url(self, candidate: Candidate) -> str:
        for link in await self._links(candidate.page_url):
            if FULL_TEXT_LINK.search(link.text) and ".pdf" in link.href.lower():
                return link.href
        raise UpstreamError(f"No full-text PDF link on {candidate.page_url}")

    async def smp(self, language: Language) -> list[Candidate]:
        """Only the latest survey report is published; issue/date come from the PDF itself."""
        page = SMP_PAGE[language]
        for link in await self._links(page):
            if SMP_PDF.search(link.href):
                title = (
                    "Piyasa Katılımcıları Anketi"
                    if language == "tr"
                    else ("Survey of Market Participants")
                )
                return [
                    Candidate(
                        doc_type="smp",
                        language=language,
                        title=title,
                        issue=None,
                        doc_date=None,
                        date_precision="month",
                        page_url=page,
                        pdf_url=link.href,
                    )
                ]
        raise UpstreamError(f"No survey report PDF link on {page}")


def _report_issue(doc_type: str, title: str) -> str | None:
    if doc_type == "ir":
        m = re.search(r"(\d{4})\s*-\s*(IV|III|II|I)\b", title)
        return f"{m[1]}-{m[2]}" if m else None
    m = re.search(r"(?:Sayı|Volume)\s*(\d+)", title)
    return m[1] if m else None
