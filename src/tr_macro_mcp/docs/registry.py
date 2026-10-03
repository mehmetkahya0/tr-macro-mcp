"""Persistent document registry: metadata, outline and per-page (sanitised) text in DuckDB."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tr_macro_mcp.docs.parse import OutlineEntry, OutlineMethod
from tr_macro_mcp.models import DocType, DocumentInfo, Language
from tr_macro_mcp.security.sanitize import SecurityFlag
from tr_macro_mcp.store.db import DuckFile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id VARCHAR PRIMARY KEY,
    doc_type VARCHAR NOT NULL,
    language VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    issue VARCHAR,
    doc_date DATE,
    date_precision VARCHAR NOT NULL,
    page_count INTEGER NOT NULL,
    url VARCHAR NOT NULL,
    page_url VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    ingested_at TIMESTAMP NOT NULL,
    local_path VARCHAR NOT NULL,
    outline_method VARCHAR NOT NULL,
    outline_json VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id VARCHAR NOT NULL,
    page INTEGER NOT NULL,
    page_label VARCHAR,
    text VARCHAR NOT NULL,
    flags_json VARCHAR NOT NULL,
    PRIMARY KEY (doc_id, page)
);
"""
_DOC_COLUMNS = (
    "doc_id, doc_type, language, title, issue, doc_date, date_precision, page_count, url, "
    "page_url, sha256, ingested_at"
)


class StoredPage(BaseModel):
    page: int
    page_label: str | None
    text: str
    flags: list[SecurityFlag]


class StoredDocument(BaseModel):
    info: DocumentInfo
    local_path: str
    outline_method: OutlineMethod
    outline: list[OutlineEntry]


class PageRow(BaseModel):
    doc_id: str
    title: str
    doc_date: dt.date | None
    page: int
    page_label: str | None
    text: str


def _info(row: tuple[Any, ...]) -> DocumentInfo:
    values = dict(zip(_DOC_COLUMNS.split(", "), row, strict=True))
    values["ingested_at"] = values["ingested_at"].replace(tzinfo=dt.UTC)
    return DocumentInfo.model_validate(values)


class DocumentRegistry:
    def __init__(self, path: Path) -> None:
        self._db = DuckFile(path, _SCHEMA)

    def upsert(
        self,
        info: DocumentInfo,
        local_path: Path,
        outline_method: OutlineMethod,
        outline: list[OutlineEntry],
        pages: list[StoredPage],
    ) -> None:
        with self._db.connect() as conn:
            conn.execute("BEGIN TRANSACTION")
            conn.execute("DELETE FROM pages WHERE doc_id = ?", [info.doc_id])
            conn.execute("DELETE FROM documents WHERE doc_id = ?", [info.doc_id])
            conn.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    info.doc_id,
                    info.doc_type,
                    info.language,
                    info.title,
                    info.issue,
                    info.doc_date,
                    info.date_precision,
                    info.page_count,
                    info.url,
                    info.page_url,
                    info.sha256,
                    info.ingested_at.astimezone(dt.UTC).replace(tzinfo=None),
                    str(local_path),
                    outline_method,
                    json.dumps([e.model_dump() for e in outline], ensure_ascii=False),
                ],
            )
            rows = [
                [
                    info.doc_id,
                    p.page,
                    p.page_label,
                    p.text,
                    json.dumps([f.model_dump() for f in p.flags]),
                ]
                for p in pages
            ]
            if rows:  # executemany rejects an empty list
                conn.executemany("INSERT INTO pages VALUES (?, ?, ?, ?, ?)", rows)
            conn.execute("COMMIT")

    def get(self, doc_id: str) -> StoredDocument | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_DOC_COLUMNS}, local_path, outline_method, outline_json "
                "FROM documents WHERE doc_id = ?",
                [doc_id],
            ).fetchone()
        if row is None:
            return None
        return StoredDocument(
            info=_info(row[:12]),
            local_path=row[12],
            outline_method=row[13],
            outline=[OutlineEntry.model_validate(e) for e in json.loads(row[14])],
        )

    def known_sources(self) -> dict[str, str]:
        """page_url -> doc_id and url -> doc_id, for idempotent ingestion."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT doc_id, page_url, url, doc_type FROM documents").fetchall()
        known: dict[str, str] = {}
        for doc_id, page_url, url, doc_type in rows:
            known[url] = doc_id
            if doc_type != "smp":  # one document per detail page; the survey page is shared
                known[page_url] = doc_id
        return known

    def sha256_exists(self, sha256: str) -> str | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT doc_id FROM documents WHERE sha256 = ?", [sha256]).fetchone()
        return str(row[0]) if row else None

    def list_documents(
        self,
        doc_type: DocType | None = None,
        language: Language | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        title_contains: str | None = None,
    ) -> list[DocumentInfo]:
        where, params = _filters(doc_type, language, date_from, date_to)
        if title_contains:
            where.append("(lower(title) LIKE ? OR lower(doc_id) LIKE ?)")
            params += [f"%{title_contains.lower()}%"] * 2
        sql = f"SELECT {_DOC_COLUMNS} FROM documents"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY doc_date DESC NULLS LAST, doc_id"
        with self._db.connect() as conn:
            return [_info(row) for row in conn.execute(sql, params).fetchall()]

    def pages(self, doc_id: str, first: int, last: int) -> list[StoredPage]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT page, page_label, text, flags_json FROM pages "
                "WHERE doc_id = ? AND page BETWEEN ? AND ? ORDER BY page",
                [doc_id, first, last],
            ).fetchall()
        return [
            StoredPage(
                page=r[0],
                page_label=r[1],
                text=r[2],
                flags=[SecurityFlag.model_validate(f) for f in json.loads(r[3])],
            )
            for r in rows
        ]

    def search_pages(
        self,
        doc_type: DocType | None,
        language: Language | None,
        date_from: dt.date | None,
        date_to: dt.date | None,
    ) -> tuple[list[PageRow], int]:
        where, params = _filters(doc_type, language, date_from, date_to, prefix="d.")
        sql = (
            "SELECT p.doc_id, d.title, d.doc_date, p.page, p.page_label, p.text "
            "FROM pages p JOIN documents d USING (doc_id)"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY d.doc_date DESC NULLS LAST, p.doc_id, p.page"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        docs = len({r[0] for r in rows})
        return [
            PageRow(doc_id=r[0], title=r[1], doc_date=r[2], page=r[3], page_label=r[4], text=r[5])
            for r in rows
        ], docs


def _filters(
    doc_type: str | None,
    language: str | None,
    date_from: dt.date | None,
    date_to: dt.date | None,
    prefix: str = "",
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    for column, value in ((f"{prefix}doc_type", doc_type), (f"{prefix}language", language)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if date_from:
        where.append(f"{prefix}doc_date >= ?")
        params.append(date_from)
    if date_to:
        where.append(f"{prefix}doc_date <= ?")
        params.append(date_to)
    return where, params
