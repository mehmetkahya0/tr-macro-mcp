"""Domain errors. Tools turn these into actionable messages for the client LLM."""


class TrMacroError(Exception):
    """Base class for errors that carry a user-facing message."""


class MissingKeyError(TrMacroError):
    def __init__(self) -> None:
        super().__init__(
            "EVDS_API_KEY not set; see README ('EVDS key'). This server is running in keyless "
            "mode: FX rates are available via get_fx_rate or series codes like 'FX:USD'."
        )


class UpstreamError(TrMacroError):
    """The data source failed or returned something we could not interpret."""


class InvalidRequestError(TrMacroError):
    """The request is malformed or outside what a source supports."""
