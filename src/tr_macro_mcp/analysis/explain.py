"""Assemble cited evidence around a series movement. Correlation in time only; no causality."""

import datetime as dt
import re
from itertools import pairwise

from pydantic import BaseModel

from tr_macro_mcp.analysis.compare import parse_decision
from tr_macro_mcp.docs.registry import DocumentRegistry
from tr_macro_mcp.models import DocumentInfo, Language, Observation

CO_OCCURRENCE_DAYS = 3
EXCERPT_CHARS = 600
TOP_MOVES = 3
FX_KEYWORDS: dict[Language, tuple[str, ...]] = {
    "en": ("exchange rate", "Turkish lira", "reserves", "currency"),
    "tr": ("döviz kuru", "Türk lirası", "rezerv", "kur geçişkenliği"),
}


class Move(BaseModel):
    from_date: dt.date
    to_date: dt.date
    change_abs: float
    change_pct: float | None


class Movement(BaseModel):
    first_date: dt.date
    first_value: float
    last_date: dt.date
    last_value: float
    change_abs: float
    change_pct: float | None
    min_date: dt.date
    min_value: float
    max_date: dt.date
    max_value: float
    largest_moves: list[Move]


class Evidence(BaseModel):
    doc_id: str
    doc_type: str
    doc_date: dt.date | None
    title: str
    url: str
    page: int
    page_label: str | None
    relevance: str
    matched_terms: list[str]
    untrusted_excerpt: str


class CoOccurrence(BaseModel):
    move: Move
    doc_id: str
    doc_date: dt.date
    days_apart: int


def movement(observations: list[Observation]) -> Movement | None:
    points = [(o.date, o.value) for o in observations if o.date and o.value is not None]
    if len(points) < 2:
        return None
    points.sort()
    moves = [
        Move(
            from_date=d0,
            to_date=d1,
            change_abs=round(v1 - v0, 6),
            change_pct=round((v1 / v0 - 1) * 100, 4) if v0 else None,
        )
        for (d0, v0), (d1, v1) in pairwise(points)
    ]
    moves.sort(
        key=lambda m: abs(m.change_pct if m.change_pct is not None else m.change_abs), reverse=True
    )
    (first_date, first), (last_date, last) = points[0], points[-1]
    low = min(points, key=lambda p: p[1])
    high = max(points, key=lambda p: p[1])
    return Movement(
        first_date=first_date,
        first_value=first,
        last_date=last_date,
        last_value=last,
        change_abs=round(last - first, 6),
        change_pct=round((last / first - 1) * 100, 4) if first else None,
        min_date=low[0],
        min_value=low[1],
        max_date=high[0],
        max_value=high[1],
        largest_moves=moves[:TOP_MOVES],
    )


def _excerpt(text: str, term: str | None) -> str:
    flat = re.sub(r"\s+", " ", text)
    if term is None:
        return flat[:EXCERPT_CHARS]
    at = flat.lower().find(term.lower())
    start = max(0, at - EXCERPT_CHARS // 3)
    return flat[start : start + EXCERPT_CHARS]


class EvidenceCollector:
    def __init__(self, registry: DocumentRegistry) -> None:
        self._registry = registry

    def _pages(self, info: DocumentInfo) -> list[tuple[int, str | None, str]]:
        return [
            (p.page, p.page_label, p.text)
            for p in self._registry.pages(info.doc_id, 1, info.page_count)
        ]

    def mpc_decisions(
        self, language: Language, start: dt.date, end: dt.date
    ) -> tuple[DocumentInfo | None, list[DocumentInfo]]:
        """The decision in force at `start` and the decisions taken within [start, end]."""
        decisions = [
            d for d in self._registry.list_documents("mpc", language) if d.doc_date is not None
        ]
        prior = [d for d in decisions if d.doc_date and d.doc_date < start]
        within = [d for d in decisions if d.doc_date and start <= d.doc_date <= end]
        return (max(prior, key=lambda d: d.doc_date or dt.date.min) if prior else None), sorted(
            within, key=lambda d: d.doc_date or dt.date.min
        )

    def decision_evidence(
        self, info: DocumentInfo, keywords: tuple[str, ...], relevance: str
    ) -> list[Evidence]:
        pages = self._pages(info)
        parsed = parse_decision([text for _, _, text in pages])
        items = []
        if parsed.sentences:
            items.append(self._item(info, pages[0], relevance, [], parsed.sentences[0]))
        for sentence in parsed.sentences[1:]:
            terms = [k for k in keywords if k.lower() in sentence.lower()]
            if terms:
                items.append(self._item(info, pages[0], "mpc_keyword_match", terms, sentence))
        return items

    def report_evidence(
        self,
        language: Language,
        start: dt.date,
        end: dt.date,
        keywords: tuple[str, ...],
        per_report: int = 2,
    ) -> list[Evidence]:
        items: list[Evidence] = []
        for doc_type in ("ir", "fsr"):
            for info in self._registry.list_documents(doc_type, language, start, end):
                scored = []
                for page in self._pages(info):
                    lowered = page[2].lower()
                    terms = [k for k in keywords if k.lower() in lowered]
                    hits = sum(lowered.count(k.lower()) for k in terms)
                    if hits:
                        scored.append((hits, page, terms))
                for _, page, terms in sorted(scored, key=lambda s: s[0], reverse=True)[:per_report]:
                    items.append(
                        self._item(
                            info, page, "report_keyword_match", terms, _excerpt(page[2], terms[0])
                        )
                    )
        return items

    @staticmethod
    def _item(
        info: DocumentInfo,
        page: tuple[int, str | None, str],
        relevance: str,
        terms: list[str],
        text: str,
    ) -> Evidence:
        return Evidence(
            doc_id=info.doc_id,
            doc_type=info.doc_type,
            doc_date=info.doc_date,
            title=info.title,
            url=info.url,
            page=page[0],
            page_label=page[1],
            relevance=relevance,
            matched_terms=terms,
            untrusted_excerpt=text[:EXCERPT_CHARS],
        )


def co_occurrences(moves: list[Move], decisions: list[DocumentInfo]) -> list[CoOccurrence]:
    found = []
    for move in moves:
        for decision in decisions:
            if decision.doc_date is None:
                continue
            gap = min(
                abs((decision.doc_date - move.from_date).days),
                abs((decision.doc_date - move.to_date).days),
            )
            if gap <= CO_OCCURRENCE_DAYS:
                found.append(
                    CoOccurrence(
                        move=move,
                        doc_id=decision.doc_id,
                        doc_date=decision.doc_date,
                        days_apart=gap,
                    )
                )
    return found
