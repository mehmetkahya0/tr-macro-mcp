"""Short-lived DuckDB connections.

DuckDB lets only one process hold a database file read-write. Several MCP clients (or the
`ingest` command next to a running server) may use the same cache, so every operation opens
its own connection and retries briefly while another process holds the lock.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from tr_macro_mcp.errors import UpstreamError

LOCK_RETRIES = 50
LOCK_WAIT_SECONDS = 0.1


class DuckFile:
    def __init__(self, path: Path, schema: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as conn:
            conn.execute(schema)

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()

    def _open(self) -> duckdb.DuckDBPyConnection:
        for _ in range(LOCK_RETRIES):
            try:
                return duckdb.connect(str(self.path))
            except duckdb.IOException as exc:
                if "lock" not in str(exc).lower():
                    raise
                time.sleep(LOCK_WAIT_SECONDS)
        raise UpstreamError(
            f"The local database {self.path.name} is locked by another process (for example a "
            "running ingestion). Try again in a moment."
        )
