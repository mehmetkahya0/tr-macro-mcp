"""Live smoke test: real stdio subprocess against real TCMB. Run with `pytest -m live`."""

import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_keyless_usd_try_over_stdio(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tr_macro_mcp.server"],
        env={**os.environ, "EVDS_API_KEY": "", "TR_MACRO_CACHE_DIR": str(tmp_path)},
    )
    async with Client(params) as client:
        sources = (await client.call_tool("list_data_sources", {})).structured_content
        rate = await client.call_tool("get_fx_rate", {"currency": "USD", "date": "2025-01-02"})
        missing = await client.call_tool("search_series", {"query": "politika faizi"})
    assert sources["keyless_mode"] is True
    assert rate.structured_content["value"] == 35.2525
    assert rate.structured_content["citations"][0]["bulletin_no"] == "2025/1"
    assert missing.is_error
    assert "EVDS_API_KEY not set" in missing.content[0].text


async def test_ingest_and_read_latest_mpc_decision(tmp_path: Path) -> None:
    from tests.conftest import make_settings
    from tr_macro_mcp.server import ingest

    settings = make_settings(tmp_path).model_copy(update={"min_request_interval": 1.0})
    report = await ingest(settings, ["mpc"], ["en"], mpc_since=2026, reports_since=2026)
    assert report.added
    assert not report.failed
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tr_macro_mcp.server"],
        env={**os.environ, "EVDS_API_KEY": "", "TR_MACRO_CACHE_DIR": str(tmp_path)},
    )
    async with Client(params) as client:
        listed = (await client.call_tool("list_documents", {"limit": 1})).structured_content
        latest = listed["documents"][0]["doc_id"]
        read = (await client.call_tool("read_document", {"doc_id": latest})).structured_content
    assert "Monetary Policy Committee" in read["pages"][0]["text"]
    assert read["citations"][0]["url"].endswith(".pdf?MOD=AJPERES")
