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
