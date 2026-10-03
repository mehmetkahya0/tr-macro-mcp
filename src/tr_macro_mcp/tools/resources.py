"""TCMB documents as MCP resources (read-only views of the local corpus)."""

import json

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

from tr_macro_mcp.docs.navigate import citation
from tr_macro_mcp.security.sanitize import wrap
from tr_macro_mcp.tools import Services


def register(mcp: MCPServer, services: Services) -> None:
    registry = services.doc_registry

    @mcp.resource(
        "tcmb://documents",
        name="tcmb-documents",
        description="Index of locally available TCMB documents (newest first).",
        mime_type="application/json",
    )
    def document_index() -> str:
        return json.dumps(
            [
                {
                    "doc_id": d.doc_id,
                    "doc_type": d.doc_type,
                    "language": d.language,
                    "doc_date": d.doc_date.isoformat() if d.doc_date else None,
                    "title": d.title,
                    "page_count": d.page_count,
                    "uri": f"tcmb://documents/{d.doc_id}",
                }
                for d in registry.list_documents()
            ],
            ensure_ascii=False,
        )

    @mcp.resource(
        "tcmb://documents/{doc_id}",
        name="tcmb-document",
        description="Metadata, citation and outline of one TCMB document.",
        mime_type="application/json",
    )
    def document(doc_id: str) -> str:
        stored = registry.get(doc_id)
        if stored is None:
            raise ResourceError(f"No document {doc_id!r}; see tcmb://documents.")
        return json.dumps(
            {
                "document": stored.info.model_dump(mode="json"),
                "citation": citation(stored.info).model_dump(mode="json"),
                "outline_method": stored.outline_method,
                "outline": [e.model_dump() for e in stored.outline],
                "pages": [
                    f"tcmb://documents/{doc_id}/pages/{n}"
                    for n in range(1, stored.info.page_count + 1)
                ],
            },
            ensure_ascii=False,
        )

    @mcp.resource(
        "tcmb://documents/{doc_id}/pages/{page}",
        name="tcmb-document-page",
        description="Text of one page, wrapped as untrusted document text, with its citation.",
        mime_type="text/plain",
    )
    def document_page(doc_id: str, page: str) -> str:
        stored = registry.get(doc_id)
        if stored is None or not page.isdigit():
            raise ResourceError(f"No page {page!r} of document {doc_id!r}.")
        found = registry.pages(doc_id, int(page), int(page))
        if not found:
            raise ResourceError(f"{doc_id} has pages 1-{stored.info.page_count}.")
        cite = citation(stored.info, found[0].page, found[0].page_label)
        header = (
            f"Source: {cite.source} | {cite.title} | {cite.doc_date} | PDF page {cite.page}"
            f" (printed {cite.page_label or 'n/a'}) | {cite.url}"
        )
        return f"{header}\n{wrap(found[0].text)}"
