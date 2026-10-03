"""Phase 3: compare_ppk_statements, explain_move, resources and prompts (offline, real fixtures)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from tests.conftest import Router

pytestmark = pytest.mark.anyio
ServerFactory = Callable[..., MCPServer]


async def call(server: MCPServer, tool: str, args: dict[str, Any]) -> Any:
    async with Client(server) as client:
        return await client.call_tool(tool, args)


async def test_compare_latest_two_decisions_en(
    mpc_corpus: Path, make_server: ServerFactory
) -> None:
    data = (await call(make_server(), "compare_ppk_statements", {})).structured_content
    assert (data["decision_a"]["doc_id"], data["decision_b"]["doc_id"]) == (
        "mpc-2026-28-en",
        "mpc-2026-38-en",
    )
    assert data["decision_sentence_b"].startswith(
        "The Monetary Policy Committee (the Committee) has decided to keep the policy rate"
    )
    removed = [c["text_a"] for c in data["changes"] if c["kind"] == "removed"]
    added = [c["text_b"] for c in data["changes"] if c["kind"] == "added"]
    assert "The underlying trend of inflation decreased slightly in June." in removed
    assert any(s.startswith("Despite monthly fluctuations") for s in added)
    assert data["unchanged_sentences"] >= 10
    assert data["members_changed"] is False
    assert data["tone"]["heuristic"] is True
    assert [c["page"] for c in data["citations"]] == [1, 1]


async def test_compare_rate_cut_shows_exact_number_changes_tr(
    mpc_corpus: Path, make_server: ServerFactory
) -> None:
    data = (
        await call(
            make_server(),
            "compare_ppk_statements",
            {"date_a": "2026-01-22", "date_b": "2026-03-12", "language": "tr"},
        )
    ).structured_content
    assert "yüzde 38’den yüzde 37’ye" in data["decision_sentence_a"]  # the January cut
    modified = [c for c in data["changes"] if c["kind"] == "modified"]
    words = {w["text"] for c in modified for w in c["word_changes"]}
    assert any("38’den" in w for w in words)


async def test_compare_rejects_non_meeting_date(
    mpc_corpus: Path, make_server: ServerFactory
) -> None:
    result = await call(make_server(), "compare_ppk_statements", {"date_b": "2026-07-24"})
    assert result.is_error
    text = result.content[0].text
    assert "No MPC decision (en) dated 2026-07-24" in text
    assert "2026-07-23" in text  # suggests, never substitutes


async def test_explain_fx_move_keyless_with_decision_in_window(
    mpc_corpus: Path, make_server: ServerFactory, router: Router
) -> None:
    data = (
        await call(
            make_server(),
            "explain_move",
            {"series_code": "FX:USD", "start": "2026-07-20", "end": "2026-07-24"},
        )
    ).structured_content
    move = data["movement"]
    assert (move["first_date"], move["last_date"]) == ("2026-07-20", "2026-07-24")
    assert move["change_abs"] == pytest.approx(move["last_value"] - move["first_value"], abs=1e-6)
    assert data["decision_in_force_at_start"]["doc_id"] == "mpc-2026-23-en"
    in_window = [e for e in data["evidence"] if e["relevance"] == "mpc_decision_in_window"]
    assert [e["doc_id"] for e in in_window] == ["mpc-2026-28-en"]
    assert in_window[0]["untrusted_excerpt"].startswith("The Monetary Policy Committee")
    assert any("causality" in n or "cause" in n for n in data["notes"])
    assert any(c["source"].startswith("TCMB indicative exchange rates") for c in data["citations"])
    assert any(c.get("page") == 1 for c in data["citations"])
    assert router.evds.requests == []


async def test_explain_evds_series_without_key(
    mpc_corpus: Path, make_server: ServerFactory
) -> None:
    result = await call(
        make_server(),
        "explain_move",
        {"series_code": "TP.TEST.RATE", "start": "2026-07-01", "end": "2026-07-31"},
    )
    assert result.is_error
    assert "EVDS_API_KEY not set" in result.content[0].text


async def test_resources(mpc_corpus: Path, make_server: ServerFactory) -> None:
    async with Client(make_server()) as client:
        templates = {
            t.uri_template for t in (await client.list_resource_templates()).resource_templates
        }
        index = await client.read_resource("tcmb://documents")
        page = await client.read_resource("tcmb://documents/mpc-2026-38-tr/pages/1")
    assert {"tcmb://documents/{doc_id}", "tcmb://documents/{doc_id}/pages/{page}"} <= templates
    assert "mpc-2026-38-tr" in index.contents[0].text
    text = page.contents[0].text
    assert text.startswith("Source: TCMB Monetary Policy Committee (PPK) decision")
    assert "<<<BEGIN UNTRUSTED DOCUMENT TEXT" in text


async def test_prompts_reference_real_tools(make_server: ServerFactory) -> None:
    async with Client(make_server()) as client:
        names = {p.name for p in (await client.list_prompts()).prompts}
        tools = {t.name for t in (await client.list_tools()).tools}
        prompt = await client.get_prompt(
            "explain_fx_move", {"currency": "eur", "start": "2026-01-01", "end": "2026-03-31"}
        )
    assert names == {"analyze_latest_mpc_decision", "explain_fx_move", "summarize_report_section"}
    text = prompt.messages[0].content.text
    assert "FX:EUR" in text
    assert "explain_move" in tools
    assert "not investment advice" in text
