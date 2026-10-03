"""Offline test harness: every HTTP request is answered from fixtures or fails the test."""

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server import MCPServer
from pydantic import SecretStr

from tr_macro_mcp.config import Settings
from tr_macro_mcp.server import create_server

FIXTURES = Path(__file__).parent / "fixtures"
FX_DIR = FIXTURES / "tcmb_fx"
DOCS_DIR = FIXTURES / "docs"
TODAY = dt.date(2026, 10, 3)  # the day fixtures were recorded (a Saturday)
TEST_KEY = "test-key-not-real"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class UnexpectedRequestError(AssertionError):
    pass


class FakeTcmb:
    """Serves recorded TCMB XML bulletins; 404 for recorded non-publication dates."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self._manifest: dict[str, int] = json.loads((FX_DIR / "manifest.json").read_text())
        self._not_found = (FX_DIR / "not_found.html").read_bytes()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "today.xml":
            return httpx.Response(200, content=(FX_DIR / "today.xml").read_bytes())
        day = dt.datetime.strptime(name.removesuffix(".xml"), "%d%m%Y").date()
        status = self._manifest.get(day.isoformat())
        if status == 200:
            return httpx.Response(200, content=(FX_DIR / name).read_bytes())
        if status == 404:
            return httpx.Response(404, content=self._not_found)
        raise UnexpectedRequestError(f"No fixture recorded for {request.url}")


class FakeEvds:
    """SYNTHETIC EVDS responses shaped like the `evds` client expects (see evds.py docstring).
    Codes and names are deliberately fake so they can never be mistaken for real data."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, Any] = {
            "datagroups/mode=0&type=json": [
                {
                    "DATAGROUP_CODE": "bie_test_rates",
                    "DATAGROUP_NAME": "Test Faiz Oranları (sentetik)",
                    "DATAGROUP_NAME_ENG": "Test Interest Rates (synthetic)",
                    "CATEGORY_ID": 99,
                },
                {
                    "DATAGROUP_CODE": "bie_test_prices",
                    "DATAGROUP_NAME": "Test Tüketici Fiyatları (sentetik)",
                    "DATAGROUP_NAME_ENG": "Test Consumer Prices (synthetic)",
                    "CATEGORY_ID": 98,
                },
            ],
            "serieList/type=json&code=bie_test_rates": [self._series_row("TP.TEST.RATE")],
            "serieList/type=json&code=TP.TEST.RATE": [self._series_row("TP.TEST.RATE")],
            "serieList/type=json&code=bie_test_prices": [
                {
                    "SERIE_CODE": "TP.TEST.CPI",
                    "DATAGROUP_CODE": "bie_test_prices",
                    "SERIE_NAME": "Test TÜFE Endeksi (sentetik)",
                    "SERIE_NAME_ENG": "Test CPI Index (synthetic)",
                    "FREQUENCY_STR": "AYLIK",
                }
            ],
        }

    @staticmethod
    def _series_row(code: str) -> dict[str, Any]:
        return {
            "SERIE_CODE": code,
            "DATAGROUP_CODE": "bie_test_rates",
            "SERIE_NAME": "Test Politika Faizi (sentetik, %)",
            "SERIE_NAME_ENG": "Test Policy Rate (synthetic, %)",
            "FREQUENCY_STR": "GÜNLÜK",
            "TAG": "politika faizi repo",
            "METADATA_LINK_ENG": "https://example.invalid/metadata",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("key") != TEST_KEY:
            return httpx.Response(401, text="Invalid API Key")
        path = request.url.raw_path.decode().split("/igmevdsms-dis/", 1)[1]
        if path in self.routes:
            return httpx.Response(200, json=self.routes[path])
        if path.startswith("series=TP.TEST.RATE&"):
            return httpx.Response(200, json=self._observations(path))
        raise UnexpectedRequestError(f"No synthetic EVDS route for {path}")

    @staticmethod
    def _observations(path: str) -> dict[str, Any]:
        params = dict(part.split("=", 1) for part in path.split("&"))
        start = dt.datetime.strptime(params["startDate"], "%d-%m-%Y").date()
        end = dt.datetime.strptime(params["endDate"], "%d-%m-%Y").date()
        days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)][-1000:]
        return {
            "totalCount": len(days),
            "items": [
                {"Tarih": f"{d:%d-%m-%Y}", "TP_TEST_RATE": f"{d.day}.5", "UNIXTIME": 0}
                for d in days
            ],
        }


class FakeTcmbDocs:
    """Serves recorded TCMB HTML pages and PDFs by exact URL (tests/fixtures/docs)."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self._manifest: dict[str, dict[str, str]] = json.loads(
            (DOCS_DIR / "manifest.json").read_text(encoding="utf-8")
        )

    def file_for(self, url: str) -> Path:
        return DOCS_DIR / self._manifest[url]["file"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url not in self._manifest:
            raise UnexpectedRequestError(f"No document fixture recorded for {url}")
        return httpx.Response(200, content=self.file_for(url).read_bytes())


class Router:
    def __init__(self) -> None:
        self.tcmb = FakeTcmb()
        self.docs = FakeTcmbDocs()
        self.evds = FakeEvds()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.tcmb.gov.tr":
            if request.url.path.startswith("/kurlar/"):
                return self.tcmb(request)
            return self.docs(request)
        if request.url.host == "evds3.tcmb.gov.tr":
            return self.evds(request)
        raise UnexpectedRequestError(f"Unexpected host {request.url.host}")


@pytest.fixture
def router() -> Router:
    return Router()


def make_settings(cache_dir: Path, key: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        evds_api_key=SecretStr(key) if key else None,
        cache_dir=cache_dir,
        min_request_interval=0,
    )


@pytest.fixture
def make_server(router: Router, tmp_path: Path) -> Callable[..., MCPServer]:
    def factory(key: str | None = None) -> MCPServer:
        return create_server(
            make_settings(tmp_path, key), transport=httpx.MockTransport(router), today=lambda: TODAY
        )

    return factory
