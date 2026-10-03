"""Decides which data sources exist in this session and routes series codes to them."""

from tr_macro_mcp.datasources.base import DataSource
from tr_macro_mcp.errors import InvalidRequestError, MissingKeyError
from tr_macro_mcp.models import DataSourceInfo


class SourceRegistry:
    def __init__(self, sources: list[DataSource]) -> None:
        self._sources = {source.name: source for source in sources}

    def infos(self) -> list[DataSourceInfo]:
        return [
            DataSourceInfo(
                name=source.name,
                description=source.description,
                coverage=source.coverage,
                requires_key=source.requires_key,
                available=source.available(),
                unavailable_reason=None
                if source.available()
                else "EVDS_API_KEY not set; see README.",
            )
            for source in self._sources.values()
        ]

    def resolve(self, series_code: str, source_name: str | None = None) -> DataSource:
        """The available source for a series code, or an actionable error."""
        if source_name is not None:
            source = self._sources.get(source_name)
            if source is None:
                raise InvalidRequestError(
                    f"Unknown source {source_name!r}; known: {sorted(self._sources)}."
                )
            if not source.handles(series_code):
                raise InvalidRequestError(f"Source {source_name!r} does not serve {series_code!r}.")
        else:
            matches = [s for s in self._sources.values() if s.handles(series_code)]
            if not matches:
                raise InvalidRequestError(f"No source serves series code {series_code!r}.")
            source = matches[0]
        if not source.available():
            raise MissingKeyError()
        return source
