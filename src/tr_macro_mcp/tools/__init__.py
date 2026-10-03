"""MCP tool registration. Tools are thin: validate, call a source, return pydantic models."""

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ParamSpec, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from tr_macro_mcp.datasources.evds import EvdsSource
from tr_macro_mcp.datasources.registry import SourceRegistry
from tr_macro_mcp.datasources.tcmb_fx_xml import TcmbXmlFxSource
from tr_macro_mcp.docs.navigate import DocumentNavigator
from tr_macro_mcp.docs.registry import DocumentRegistry
from tr_macro_mcp.errors import TrMacroError

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class Services:
    registry: SourceRegistry
    fx: TcmbXmlFxSource
    evds: EvdsSource
    documents: DocumentNavigator
    doc_registry: DocumentRegistry


def tool_errors(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Turn domain errors into ToolError so their message reaches the client LLM."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except TrMacroError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper
