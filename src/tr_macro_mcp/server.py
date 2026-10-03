"""MCP server entry point (stdio)."""

import datetime as dt
import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from mcp.server import MCPServer

from tr_macro_mcp import __version__
from tr_macro_mcp.config import Settings
from tr_macro_mcp.datasources.evds import EvdsSource
from tr_macro_mcp.datasources.registry import SourceRegistry
from tr_macro_mcp.datasources.tcmb_fx_xml import TcmbXmlFxSource, istanbul_today
from tr_macro_mcp.disclaimer import DISCLAIMER
from tr_macro_mcp.httpclient import PoliteClient
from tr_macro_mcp.store.timeseries import Store
from tr_macro_mcp.tools import Services, fx, series, sources

INSTRUCTIONS = f"""\
Turkish macroeconomic data from the Central Bank of the Republic of Türkiye (TCMB), with citations.

- Keyless: official TCMB exchange rates (get_fx_rate; series codes 'FX:<CUR>').
- With EVDS_API_KEY: the EVDS catalogue (search_series, get_series, compare_periods).
- Call list_data_sources to see what is available in this session.
- Every result has a 'citations' list: cite it in answers. If a tool reports 'not_published' or
  'not found', say so; never substitute another date or invent a number.

{DISCLAIMER}"""


def create_server(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    today: Callable[[], dt.date] = istanbul_today,
) -> MCPServer:
    """Build the server. `transport` and `today` exist for offline tests."""
    http = PoliteClient(
        min_interval=settings.min_request_interval,
        timeout=settings.http_timeout,
        transport=transport,
    )
    store = Store(settings.cache_dir / "cache.duckdb")
    fx_source = TcmbXmlFxSource(http, store, today)
    evds_source = EvdsSource(http, store, settings.evds_api_key)
    services = Services(
        registry=SourceRegistry([fx_source, evds_source]), fx=fx_source, evds=evds_source
    )

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await http.aclose()

    mcp = MCPServer(
        "tr-macro-mcp", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan
    )
    for module in (sources, fx, series):
        module.register(mcp, services)
    return mcp


def main() -> None:
    logging.basicConfig(  # stdout belongs to the MCP protocol
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings()
    logging.getLogger(__name__).info(
        "Starting tr-macro-mcp %s (%s mode), cache: %s",
        __version__,
        "EVDS" if settings.has_evds_key else "keyless",
        settings.cache_dir,
    )
    create_server(settings).run(transport="stdio")


if __name__ == "__main__":
    main()
