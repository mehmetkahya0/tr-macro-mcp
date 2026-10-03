"""End-to-end through the MCP protocol (in-memory client), fully offline."""

from collections.abc import Callable
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from tests.conftest import TEST_KEY, Router

pytestmark = pytest.mark.anyio

ServerFactory = Callable[..., MCPServer]
EXPECTED_TOOLS = {
    "list_data_sources",
    "get_fx_rate",
    "search_series",
    "get_series",
    "compare_periods",
}


async def call(server: MCPServer, tool: str, args: dict[str, Any]) -> Any:
    async with Client(server) as client:
        return await client.call_tool(tool, args)


async def test_tools_have_descriptions_and_output_schemas(make_server: ServerFactory) -> None:
    async with Client(make_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert tool.description, tool.name
        assert len(tool.description) > 80, tool.name
        assert tool.output_schema is not None, tool.name


async def test_list_data_sources_keyless(make_server: ServerFactory) -> None:
    result = await call(make_server(), "list_data_sources", {})
    data = result.structured_content
    assert data["keyless_mode"] is True
    sources = {s["name"]: s for s in data["sources"]}
    assert sources["tcmb_fx_xml"]["available"] is True
    assert sources["evds"]["available"] is False
    assert "EVDS_API_KEY" in sources["evds"]["unavailable_reason"]
    assert "not investment advice" in data["disclaimer"]


async def test_list_data_sources_with_key(make_server: ServerFactory) -> None:
    data = (await call(make_server(TEST_KEY), "list_data_sources", {})).structured_content
    assert data["keyless_mode"] is False


async def test_usd_try_on_a_date_without_key(make_server: ServerFactory) -> None:
    result = await call(make_server(), "get_fx_rate", {"currency": "USD", "date": "2025-01-02"})
    assert not result.is_error
    data = result.structured_content
    assert data["status"] == "ok"
    assert data["value"] == 35.2525
    citation = data["citations"][0]
    assert citation["url"] == "https://www.tcmb.gov.tr/kurlar/202501/02012025.xml"
    assert citation["retrieved_at"]


async def test_holiday_is_explicit_not_substituted(make_server: ServerFactory) -> None:
    data = (
        await call(make_server(), "get_fx_rate", {"currency": "USD", "date": "2025-01-01"})
    ).structured_content
    assert data["status"] == "not_published"
    assert data["value"] is None
    assert data["nearest_earlier_bulletin"] == "2024-12-31"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("search_series", {"query": "politika faizi"}),
        ("get_series", {"series_code": "TP.TEST.RATE", "start": "2025-01-01", "end": "2025-01-02"}),
        (
            "compare_periods",
            {
                "series_code": "TP.TEST.RATE",
                "period_a_start": "2025-01-01",
                "period_a_end": "2025-01-02",
                "period_b_start": "2025-02-01",
                "period_b_end": "2025-02-02",
            },
        ),
    ],
)
async def test_evds_tools_without_key_return_actionable_error(
    make_server: ServerFactory, router: Router, tool: str, args: dict[str, Any]
) -> None:
    result = await call(make_server(), tool, args)
    assert result.is_error
    assert "EVDS_API_KEY not set; see README" in result.content[0].text
    assert router.evds.requests == []


async def test_policy_rate_style_query_with_key(make_server: ServerFactory) -> None:
    server = make_server(TEST_KEY)
    async with Client(server) as client:
        found = (
            await client.call_tool("search_series", {"query": "policy rate"})
        ).structured_content
        code = found["hits"][0]["series_code"]
        series = await client.call_tool(
            "get_series", {"series_code": code, "start": "2025-01-02", "end": "2025-01-02"}
        )
    data = series.structured_content
    assert data["observations"] == [{"period": "02-01-2025", "date": "2025-01-02", "value": 2.5}]
    assert data["citations"][0]["series_code"] == "TP.TEST.RATE"


async def test_compare_periods_keyless_fx(make_server: ServerFactory) -> None:
    result = await call(
        make_server(),
        "compare_periods",
        {
            "series_code": "FX:USD",
            "period_a_start": "2024-12-30",
            "period_a_end": "2024-12-31",
            "period_b_start": "2025-01-02",
            "period_b_end": "2025-01-03",
        },
    )
    assert not result.is_error, result.content
    data = result.structured_content
    assert data["period_a"]["count"] == 2
    assert data["period_b"]["count"] == 2
    assert data["last_change_abs"] is not None
    assert data["citations"]


async def test_invalid_input_is_a_tool_error(make_server: ServerFactory) -> None:
    result = await call(
        make_server(),
        "get_series",
        {"series_code": "FX:USD:mid", "start": "2025-01-01", "end": "2025-01-02"},
    )
    assert result.is_error
    assert "rate_type" in result.content[0].text
