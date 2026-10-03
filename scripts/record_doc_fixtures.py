"""Record real TCMB pages and PDFs for the offline document tests.

Run: uv run python scripts/record_doc_fixtures.py
Writes tests/fixtures/docs/<file> plus manifest.json mapping each URL to its file. Large
report PDFs are stored as page excerpts (marked "excerpt" in the manifest).
Data © TCMB, stored as small attributed test fixtures (see docs/data-sources.md).
"""

import datetime as dt
import hashlib
import json
from pathlib import Path

import anyio
import pypdfium2 as pdfium

from tr_macro_mcp.docs.fetch import (
    MPC_EN_PRESS,
    MPC_TR_INDEX,
    MPC_TR_PRESS,
    REPORT_INDEX,
    SMP_PAGE,
    Discoverer,
)
from tr_macro_mcp.httpclient import PoliteClient

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "docs"
TODAY = dt.date(2026, 10, 3)
SINCE = 2026
EXCERPT_PAGES = {"ir": 10, "fsr": 10, "smp": 2}


def excerpt(data: bytes, pages: int) -> bytes:
    source = pdfium.PdfDocument(data)
    target = pdfium.PdfDocument.new()
    target.import_pages(source, list(range(min(pages, len(source)))))
    path = OUT / "_tmp.pdf"
    target.save(str(path))
    result = path.read_bytes()
    path.unlink()
    return result


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, str]] = {}
    http = PoliteClient(min_interval=1.0, timeout=60)

    async def save(url: str, *, kind: str = "full", pages: int | None = None) -> bytes:
        response = await http.get(url)
        response.raise_for_status()
        data = response.content
        if pages is not None:
            data = excerpt(data, pages)
            kind = f"excerpt: first {pages} pages"
        suffix = ".pdf" if data.startswith(b"%PDF") else ".html"
        name = hashlib.sha256(url.encode()).hexdigest()[:16] + suffix
        (OUT / name).write_bytes(data)
        manifest[url] = {"file": name, "kind": kind}
        print(f"{name}  {len(data):>8}  {kind:24} {url[:90]}")
        return data

    discoverer = Discoverer(http, TODAY)
    try:
        for url in [
            MPC_TR_INDEX,
            MPC_TR_PRESS.format(year=2025),
            MPC_TR_PRESS.format(year=2026),
            MPC_EN_PRESS.format(year=2026),
            *REPORT_INDEX.values(),
            *SMP_PAGE.values(),
        ]:
            await save(url)
        for language in ("tr", "en"):
            for candidate in await discoverer.mpc(language, SINCE):
                await save(candidate.page_url)
                await save(await discoverer.mpc_pdf_url(candidate))
        for doc_type, language in (("ir", "en"), ("fsr", "tr")):
            candidates = await discoverer.report(doc_type, language, SINCE)  # type: ignore[arg-type]
            latest = candidates[-1]
            await save(latest.page_url)
            await save(await discoverer.report_pdf_url(latest), pages=EXCERPT_PAGES[doc_type])
        smp = (await discoverer.smp("en"))[0]
        assert smp.pdf_url
        await save(smp.pdf_url, pages=EXCERPT_PAGES["smp"])
    finally:
        await http.aclose()
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    anyio.run(main)
