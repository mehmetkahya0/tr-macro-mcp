"""Parsing and discovery against recorded real TCMB pages/PDFs (tests/fixtures/docs)."""

import datetime as dt

import httpx
import pytest

from tests.conftest import TODAY, FakeTcmbDocs
from tr_macro_mcp.docs.fetch import (
    Discoverer,
    canonical_url,
    extract_links,
    parse_date,
)
from tr_macro_mcp.docs.parse import parse_pdf
from tr_macro_mcp.httpclient import PoliteClient

pytestmark = pytest.mark.anyio
WCM = "https://www.tcmb.gov.tr/wps/wcm/connect"
IR_PDF = f"{WCM}/aaa0e73a-2e5d-42f2-bf76-22f17aae0882/Inflation+Report2026iii.pdf?MOD=AJPERES"
DUY01_PDF = f"{WCM}/584a42c8-8f16-43b5-9a72-4cc764b44c62/DUY2026-01.pdf?MOD=AJPERES"


@pytest.fixture
def docs() -> FakeTcmbDocs:
    return FakeTcmbDocs()


@pytest.fixture
def discoverer(docs: FakeTcmbDocs) -> Discoverer:
    return Discoverer(
        PoliteClient(min_interval=0, timeout=5, transport=httpx.MockTransport(docs)), TODAY
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Enflasyon Raporu 2026 - III (13 Ağustos 2026)", (dt.date(2026, 8, 13), "day")),
        ("Inflation Report 2026 - III (August 13, 2026)", (dt.date(2026, 8, 13), "day")),
        (
            "Faiz Oranlarına İlişkin Basın Duyurusu – 21/01/2014, (2014-01)",
            (dt.date(2014, 1, 21), "day"),
        ),
        ("Finansal İstikrar Raporu - Mayıs 2026, Sayı 42", (dt.date(2026, 5, 1), "month")),
        ("NİSAN 2026", (dt.date(2026, 4, 1), "month")),
        ("SEPTEMBER 2026", (dt.date(2026, 9, 1), "month")),
        ("no date here", None),
    ],
)
def test_parse_date(text: str, expected: tuple[dt.date, str] | None) -> None:
    assert parse_date(text) == expected


def test_extract_links_resolves_and_canonicalises() -> None:
    html = (
        '<a href="/x/A.pdf?MOD=AJPERES&amp;CACHEID=ROOT-1">  Full\n text </a>'
        '<a href="javascript:x()">j</a>'
    )
    assert [(link.href, link.text) for link in extract_links(html, "https://www.tcmb.gov.tr")] == [
        ("https://www.tcmb.gov.tr/x/A.pdf?MOD=AJPERES", "Full text")
    ]
    assert canonical_url("u?MOD=AJPERES&CACHEID=abc") == "u?MOD=AJPERES"


async def test_mpc_tr_uses_index_and_press_lists(discoverer: Discoverer) -> None:
    candidates = await discoverer.mpc("tr", 2026)
    assert [c.issue for c in candidates] == [
        "2026-01",
        "2026-12",
        "2026-17",
        "2026-23",
        "2026-28",
        "2026-38",
    ]
    assert "/Duyurular/Basin/2026/DUY2026-38" in candidates[-1].page_url  # missing from PPK index


async def test_mpc_en_and_pdf_link(discoverer: Discoverer) -> None:
    candidates = await discoverer.mpc("en", 2026)
    assert candidates[0].issue == "2026-01"
    assert candidates[0].title.startswith("Press Release on Interest Rates")
    tr = (await discoverer.mpc("tr", 2026))[0]
    assert await discoverer.mpc_pdf_url(tr) == DUY01_PDF


async def test_report_discovery_and_full_text_link(discoverer: Discoverer) -> None:
    reports = await discoverer.report("ir", "en", 2026)
    assert [r.issue for r in reports] == ["2026-I", "2026-II", "2026-III"]
    assert reports[-1].doc_date == dt.date(2026, 8, 13)
    assert await discoverer.report_pdf_url(reports[-1]) == IR_PDF
    fsr = await discoverer.report("fsr", "tr", 2026)
    assert [(r.issue, r.doc_date, r.date_precision) for r in fsr] == [
        ("42", dt.date(2026, 5, 1), "month")
    ]


async def test_smp_points_to_latest_report(discoverer: Discoverer) -> None:
    (candidate,) = await discoverer.smp("en")
    assert candidate.pdf_url
    assert candidate.pdf_url.endswith("/SMP_Report.pdf?MOD=AJPERES")


class TestParsePdf:
    def test_report_page_labels_and_contents_outline(self, docs: FakeTcmbDocs) -> None:
        parsed = parse_pdf(docs.file_for(IR_PDF).read_bytes())  # first 10 pages of IR 2026-III
        assert parsed.page_labels[:5] == [None, None, "1", "2", "3"]
        assert parsed.outline_method == "contents_page"
        by_title = {e.title: e for e in parsed.outline}
        assert (by_title["1. Overview"].page, by_title["1. Overview"].page_label) == (3, "1")
        global_economy = by_title["2.1 Global Economy"]
        assert global_economy.level == 2
        assert global_economy.page is not None
        assert "2.1 Global Economy" in parsed.pages[global_economy.page - 1]

    def test_mpc_decision_text_is_clean_turkish(self, docs: FakeTcmbDocs) -> None:
        parsed = parse_pdf(docs.file_for(DUY01_PDF).read_bytes())
        assert len(parsed.pages) == 1
        assert "Para Politikası Kurulu Kararı" in parsed.pages[0]
        assert "yüzde 38’den" in parsed.pages[0]
        assert parse_date(parsed.pages[0]) == (dt.date(2026, 1, 22), "day")
