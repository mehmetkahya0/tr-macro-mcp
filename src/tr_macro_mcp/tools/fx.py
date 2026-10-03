import datetime as dt
from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from tr_macro_mcp.models import FxRateResult, FxRateType
from tr_macro_mcp.tools import Services, tool_errors


def register(mcp: MCPServer, services: Services) -> None:
    @mcp.tool()
    @tool_errors
    async def get_fx_rate(
        currency: Annotated[
            str, Field(description="ISO code, e.g. 'USD', 'EUR', 'GBP', 'JPY', 'XDR'.")
        ],
        date: Annotated[
            dt.date | None,
            Field(description="Bulletin date (YYYY-MM-DD). Omit for the latest bulletin."),
        ] = None,
        rate_type: Annotated[
            FxRateType, Field(description="Which rate goes into 'value'. All four are in 'quote'.")
        ] = "forex_buying",
    ) -> FxRateResult:
        """Official TCMB indicative exchange rate of a currency against the Turkish lira
        (TRY per 1 unit) for a date. Works without any API key.

        TCMB publishes one bulletin per business day around 15:30 Istanbul time. If no bulletin
        exists for the date (weekend, holiday, not yet published), status is 'not_published' and
        no rate is returned; 'nearest_earlier_bulletin' names the closest earlier date with a
        bulletin, which you may query explicitly. Never present that date's rate as the
        requested date's rate. Cite the returned citation (bulletin URL and number)."""
        return await services.fx.get_rate(currency, date, rate_type)
