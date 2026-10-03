"""PDF → pages, printed page labels and outline, using pypdfium2 (BSD/Apache; ADR 0002).

TCMB report PDFs have no bookmarks; printed page numbers end the running header
("Inflation Report 2026-III | Overview 3") and differ from PDF page indices. The outline is
taken from embedded bookmarks when present, otherwise from the dotted-leader Contents page.
"""

import re
from collections import Counter
from typing import Literal

import pypdfium2 as pdfium
from pydantic import BaseModel

from tr_macro_mcp.errors import UpstreamError

OutlineMethod = Literal["bookmarks", "contents_page", "none"]

_HEADER_NUMBER = re.compile(r"(?:^|\s|\|)(\d{1,3})\s*$")
_CONTENTS_TITLE = re.compile(r"^\s*(contents|table of contents|içindekiler)\s*$", re.IGNORECASE)
_CONTENTS_LINE = re.compile(r"^(?P<title>\S.*?)\s*[.…·]{3,}[.…·\s]*(?P<page>\d{1,3})\s*$")
_LEVEL2 = re.compile(r"^(\d+\.\d+|[IVX]+\.\d+|(Box|Kutu|Zoom-In|Odak)\b)", re.IGNORECASE)
CONTENTS_SEARCH_PAGES = 8


class OutlineEntry(BaseModel):
    title: str
    level: int
    page: int | None  # 1-based PDF page
    page_label: str | None


class ParsedPdf(BaseModel):
    pages: list[str]
    page_labels: list[str | None]
    outline: list[OutlineEntry]
    outline_method: OutlineMethod


def parse_pdf(data: bytes) -> ParsedPdf:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise UpstreamError(f"Not a readable PDF: {exc}") from exc
    try:
        pages = [_page_text(pdf, i) for i in range(len(pdf))]
        labels = page_labels(pages)
        outline = _bookmarks(pdf, labels)
    finally:
        pdf.close()
    if outline:
        return ParsedPdf(
            pages=pages, page_labels=labels, outline=outline, outline_method="bookmarks"
        )
    outline = contents_outline(pages, labels)
    return ParsedPdf(
        pages=pages,
        page_labels=labels,
        outline=outline,
        outline_method="contents_page" if outline else "none",
    )


def _page_text(pdf: pdfium.PdfDocument, index: int) -> str:
    page = pdf[index]
    try:
        textpage = page.get_textpage()
        try:
            return str(textpage.get_text_range()).replace("\r\n", "\n").replace("\ufffe", "-")
        finally:
            textpage.close()
    finally:
        page.close()


def _edge_number(text: str) -> int | None:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in (lines[:1] + lines[-1:]) if lines else []:
        match = _HEADER_NUMBER.search(line)
        if match:
            return int(match.group(1))
    return None


def page_labels(pages: list[str]) -> list[str | None]:
    """Printed page numbers, only where the page itself shows the number implied by the
    document's dominant offset (printed = pdf_page + offset)."""
    candidates = [_edge_number(text) for text in pages]
    offsets = Counter(n - (i + 1) for i, n in enumerate(candidates) if n is not None)
    if not offsets:
        return [None] * len(pages)
    offset, support = offsets.most_common(1)[0]
    if support < max(3, len(pages) // 4):
        return [None] * len(pages)
    return [
        str(n) if n is not None and n - (i + 1) == offset else None
        for i, n in enumerate(candidates)
    ]


def _pdf_page_for_label(label: int, labels: list[str | None]) -> int | None:
    for i, value in enumerate(labels):
        if value == str(label):
            return i + 1
    known = [(i + 1, int(v)) for i, v in enumerate(labels) if v is not None]
    if not known:
        return None
    pdf_page, printed = known[0]  # offset is constant by construction
    return label + (pdf_page - printed)


def contents_outline(pages: list[str], labels: list[str | None]) -> list[OutlineEntry]:
    entries: list[OutlineEntry] = []
    for text in pages[:CONTENTS_SEARCH_PAGES]:
        lines = text.split("\n")
        if not any(_CONTENTS_TITLE.match(line) for line in lines[:6]):
            continue
        for line in lines:
            match = _CONTENTS_LINE.match(line.strip())
            if not match:
                continue
            title = re.sub(r"[.…·\s]+$", "", match.group("title")).strip()
            printed = int(match.group("page"))
            entries.append(
                OutlineEntry(
                    title=title,
                    level=2 if _LEVEL2.match(title) else 1,
                    page=_pdf_page_for_label(printed, labels),
                    page_label=str(printed),
                )
            )
        if entries:
            break
    return [e for e in entries if e.page is None or 1 <= e.page <= len(pages)]


def _bookmarks(pdf: pdfium.PdfDocument, labels: list[str | None]) -> list[OutlineEntry]:
    entries: list[OutlineEntry] = []
    for bookmark in pdf.get_toc():
        dest = bookmark.get_dest()
        index = dest.get_index() if dest is not None else None
        page = index + 1 if index is not None else None
        entries.append(
            OutlineEntry(
                title=bookmark.get_title().strip(),
                level=int(getattr(bookmark, "level", 0)) + 1,
                page=page,
                page_label=labels[page - 1] if page and page <= len(labels) else None,
            )
        )
    # Slide decks export one "Slide N" bookmark per page: no structural information.
    if entries and all(re.fullmatch(r"Slide \d+", e.title) for e in entries):
        return []
    return entries
