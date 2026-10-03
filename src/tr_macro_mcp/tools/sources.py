from mcp.server import MCPServer

from tr_macro_mcp.disclaimer import DISCLAIMER
from tr_macro_mcp.models import DataSourcesResult
from tr_macro_mcp.tools import Services, tool_errors


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @tool_errors
    async def list_data_sources() -> DataSourcesResult:
        """List the numeric data sources this server knows, what each covers, whether it needs
        an API key, and whether it is available in this session. Call this first if unsure
        whether EVDS (key required) can be used or only keyless FX rates."""
        infos = services.registry.infos()
        return DataSourcesResult(
            sources=infos,
            keyless_mode=not any(i.available for i in infos if i.requires_key),
            disclaimer=DISCLAIMER,
        )
