"""Runtime settings, read from environment variables and an optional `.env` file."""

from pathlib import Path

from platformdirs import user_cache_dir
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from tr_macro_mcp import __version__

USER_AGENT = f"tr-macro-mcp/{__version__} (open-source MCP server for TCMB data; local use)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    evds_api_key: SecretStr | None = Field(
        default=None,
        description="Optional EVDS key. Without it the server runs in keyless mode.",
    )
    cache_dir: Path = Field(
        default=Path(user_cache_dir("tr-macro-mcp", appauthor=False)),
        validation_alias="TR_MACRO_CACHE_DIR",
    )
    min_request_interval: float = Field(
        default=1.0,
        ge=0.0,
        validation_alias="TR_MACRO_MIN_REQUEST_INTERVAL",
        description="Minimum seconds between two requests to the same host.",
    )
    http_timeout: float = Field(default=30.0, gt=0, validation_alias="TR_MACRO_HTTP_TIMEOUT")

    @property
    def has_evds_key(self) -> bool:
        return self.evds_api_key is not None and bool(self.evds_api_key.get_secret_value())
