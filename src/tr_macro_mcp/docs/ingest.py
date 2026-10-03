"""Idempotent, polite ingestion: discover → download PDF → parse → sanitise → register.

Re-running only re-reads index pages. MPC/IR/FSR documents are skipped by detail-page URL;
the survey (SMP) PDF URL never changes, so it is downloaded and compared by SHA-256.
"""

import hashlib
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from tr_macro_mcp.docs.fetch import Candidate, Discoverer, parse_date
from tr_macro_mcp.docs.parse import parse_pdf
from tr_macro_mcp.docs.registry import DocumentRegistry, StoredPage
from tr_macro_mcp.errors import TrMacroError, UpstreamError
from tr_macro_mcp.httpclient import PoliteClient
from tr_macro_mcp.models import DocType, DocumentInfo, Language
from tr_macro_mcp.security.sanitize import neutralise
from tr_macro_mcp.store.timeseries import utcnow

log = logging.getLogger(__name__)
MAX_PDF_BYTES = 60 * 1024 * 1024
ALL_TYPES: tuple[DocType, ...] = ("mpc", "ir", "fsr", "smp")
ALL_LANGUAGES: tuple[Language, ...] = ("tr", "en")


class IngestReport(BaseModel):
    added: list[str] = Field(default_factory=list)
    skipped: int = 0
    failed: list[str] = Field(default_factory=list)


def make_doc_id(candidate: Candidate) -> str:
    return f"{candidate.doc_type}-{candidate.issue or 'unknown'}-{candidate.language}"


class Ingestor:
    def __init__(self, http: PoliteClient, registry: DocumentRegistry, docs_dir: Path) -> None:
        self._http = http
        self._registry = registry
        self._docs_dir = docs_dir

    async def run(
        self,
        discoverer: Discoverer,
        types: tuple[DocType, ...] = ALL_TYPES,
        languages: tuple[Language, ...] = ALL_LANGUAGES,
        mpc_since: int = 2014,
        reports_since: int = 2022,
    ) -> IngestReport:
        report = IngestReport()
        for language in languages:
            for doc_type in types:
                try:
                    if doc_type == "mpc":
                        candidates = await discoverer.mpc(language, mpc_since)
                    elif doc_type == "smp":
                        candidates = await discoverer.smp(language)
                    else:
                        candidates = await discoverer.report(doc_type, language, reports_since)
                except TrMacroError as exc:
                    report.failed.append(f"discovery {doc_type}/{language}: {exc}")
                    continue
                log.info("%s/%s: %d candidates", doc_type, language, len(candidates))
                for candidate in candidates:
                    await self._ingest_one(discoverer, candidate, report)
        return report

    async def _ingest_one(
        self, discoverer: Discoverer, candidate: Candidate, report: IngestReport
    ) -> None:
        known = self._registry.known_sources()
        if candidate.doc_type != "smp" and candidate.page_url in known:
            report.skipped += 1
            return
        try:
            pdf_url = candidate.pdf_url or await (
                discoverer.mpc_pdf_url(candidate)
                if candidate.doc_type == "mpc"
                else discoverer.report_pdf_url(candidate)
            )
            data = await self._download(pdf_url)
            sha256 = hashlib.sha256(data).hexdigest()
            if self._registry.sha256_exists(sha256):
                report.skipped += 1
                return
            doc_id = self.store(candidate.model_copy(update={"pdf_url": pdf_url}), data, sha256)
        except TrMacroError as exc:
            log.warning("failed %s: %s", candidate.page_url, exc)
            report.failed.append(f"{candidate.title}: {exc}")
            return
        log.info("added %s", doc_id)
        report.added.append(doc_id)

    async def _download(self, url: str) -> bytes:
        response = await self._http.get(url)
        if response.status_code != 200:
            raise UpstreamError(f"HTTP {response.status_code} for {url}")
        data = response.content
        if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
            raise UpstreamError(f"{url} is not a PDF of acceptable size")
        return data

    def store(self, candidate: Candidate, data: bytes, sha256: str | None = None) -> str:
        """Parse, sanitise and register one PDF. Also used for local test fixtures."""
        parsed = parse_pdf(data)
        if candidate.doc_date is None and parsed.pages:
            found = parse_date(parsed.pages[0][:600])
            if found:
                candidate = candidate.model_copy(
                    update={"doc_date": found[0], "date_precision": found[1]}
                )
        if candidate.doc_type == "smp" and candidate.doc_date:
            candidate = candidate.model_copy(
                update={
                    "issue": f"{candidate.doc_date:%Y-%m}",
                    "title": f"{candidate.title} – {candidate.doc_date:%Y-%m}",
                }
            )
        doc_id = make_doc_id(candidate)
        pages: list[StoredPage] = []
        for number, (text, label) in enumerate(
            zip(parsed.pages, parsed.page_labels, strict=True), start=1
        ):
            clean = neutralise(text, page=number)
            pages.append(
                StoredPage(page=number, page_label=label, text=clean.text, flags=clean.flags)
            )
        self._docs_dir.mkdir(parents=True, exist_ok=True)
        local_path = self._docs_dir / f"{doc_id}.pdf"
        local_path.write_bytes(data)
        info = DocumentInfo(
            doc_id=doc_id,
            doc_type=candidate.doc_type,
            language=candidate.language,
            title=candidate.title,
            issue=candidate.issue,
            doc_date=candidate.doc_date,
            date_precision=candidate.date_precision,
            page_count=len(pages),
            url=candidate.pdf_url or candidate.page_url,
            page_url=candidate.page_url,
            sha256=sha256 or hashlib.sha256(data).hexdigest(),
            ingested_at=utcnow(),
        )
        self._registry.upsert(info, local_path, parsed.outline_method, parsed.outline, pages)
        return doc_id
