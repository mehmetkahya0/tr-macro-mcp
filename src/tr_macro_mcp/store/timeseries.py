"""DuckDB-backed local cache with retrieval timestamps ("as of") and TTLs."""

import datetime as dt
import threading
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel

BulletinStatus = Literal["ok", "not_published"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fx_bulletins (
    bulletin_date DATE PRIMARY KEY,
    status VARCHAR NOT NULL,
    bulletin_no VARCHAR,
    url VARCHAR NOT NULL,
    payload VARCHAR,
    retrieved_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS response_cache (
    cache_key VARCHAR PRIMARY KEY,
    body BLOB NOT NULL,
    retrieved_at TIMESTAMP NOT NULL
);
"""


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _to_naive_utc(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.UTC).replace(tzinfo=None)


def _from_naive_utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC)


class CachedBulletin(BaseModel):
    bulletin_date: dt.date
    status: BulletinStatus
    bulletin_no: str | None
    url: str
    payload: str | None
    retrieved_at: dt.datetime


class CachedResponse(BaseModel):
    body: bytes
    retrieved_at: dt.datetime


class Store:
    """Single-file cache. Use ':memory:' in tests."""

    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(path))
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)

    def get_bulletin(self, day: dt.date) -> CachedBulletin | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT bulletin_date, status, bulletin_no, url, payload, retrieved_at "
                "FROM fx_bulletins WHERE bulletin_date = ?",
                [day],
            ).fetchone()
        if row is None:
            return None
        return CachedBulletin(
            bulletin_date=row[0],
            status=row[1],
            bulletin_no=row[2],
            url=row[3],
            payload=row[4],
            retrieved_at=_from_naive_utc(row[5]),
        )

    def put_bulletin(self, bulletin: CachedBulletin) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO fx_bulletins VALUES (?, ?, ?, ?, ?, ?)",
                [
                    bulletin.bulletin_date,
                    bulletin.status,
                    bulletin.bulletin_no,
                    bulletin.url,
                    bulletin.payload,
                    _to_naive_utc(bulletin.retrieved_at),
                ],
            )

    def get_response(self, key: str, max_age: dt.timedelta) -> CachedResponse | None:
        """Return a cached body if it is younger than max_age."""
        with self._lock:
            row = self._conn.execute(
                "SELECT body, retrieved_at FROM response_cache WHERE cache_key = ?", [key]
            ).fetchone()
        if row is None:
            return None
        retrieved_at = _from_naive_utc(row[1])
        if utcnow() - retrieved_at > max_age:
            return None
        return CachedResponse(body=bytes(row[0]), retrieved_at=retrieved_at)

    def put_response(self, key: str, body: bytes, retrieved_at: dt.datetime) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO response_cache VALUES (?, ?, ?)",
                [key, body, _to_naive_utc(retrieved_at)],
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
