"""Deterministic sentence-level diff of two MPC (PPK) decision texts.

The diff itself is exact (difflib on sentences, then on words). The tone labels are a
transparent keyword heuristic and are always marked as such.
"""

import difflib
import re
from typing import Literal

from pydantic import BaseModel

from tr_macro_mcp.models import Language

ChangeKind = Literal["added", "removed", "modified"]
Tone = Literal["hardened", "softened", "no_change"]

MODIFIED_THRESHOLD = 0.6
_MEMBERS_HEADER = re.compile(
    r"^(participating committee members|toplantıya katılan kurul üyeleri)", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ“\"(])")

# Lexicon v1: stems, matched on the lower-cased (Turkish-folded) sentence. Hawkish terms point
# to tighter policy or higher inflation risk; dovish terms to easing or improving inflation.
LEXICON_VERSION = "lexicon-v1"
HAWKISH: dict[Language, tuple[str, ...]] = {
    "en": (
        "tighten",
        "tightness",
        "upside risk",
        "upward risk",
        "deteriorat",
        "elevated",
        "increase the policy rate",
        "raise",
        "hike",
        "vigilant",
        "attentive",
        "inflationary pressure",
        "strengthen",
    ),
    "tr": (
        "sıkılaş",
        "sıkılık",
        "yukarı yönlü risk",
        "bozulma",
        "yüksek seyir",
        "artır",
        "faiz artış",
        "dikkat",
        "enflasyonist baskı",
        "güçlendir",
    ),
}
DOVISH: dict[Language, tuple[str, ...]] = {
    "en": (
        "decelerat",
        "eas",
        "declin",
        "decrease",
        "improv",
        "reduce",
        "lower",
        "moderat",
        "weakness",
        "weakening",
        "cut",
    ),
    "tr": (
        "yavaşla",
        "gevşe",
        "geriled",
        "gerile",
        "düşüş",
        "iyileş",
        "indir",
        "ılımlı",
        "zayıf",
        "azal",
    ),
}


class ParsedDecision(BaseModel):
    header: str
    members: str | None
    sentences: list[str]


class WordChange(BaseModel):
    op: Literal["removed", "added"]
    text: str


class SentenceChange(BaseModel):
    kind: ChangeKind
    text_a: str | None
    text_b: str | None
    similarity: float | None
    word_changes: list[WordChange]
    tone: Tone
    hawkish_terms: list[str]
    dovish_terms: list[str]


class DecisionDiff(BaseModel):
    changes: list[SentenceChange]
    unchanged_count: int
    members_changed: bool
    tone_score: int  # sum of per-change tone deltas: >0 more hawkish wording, <0 more dovish


def _join_lines(lines: list[str]) -> str:
    text = ""
    for line in (line.strip() for line in lines):
        if not line:
            continue
        if text.endswith("-") and len(text) > 1 and text[-2].isalpha():
            text += line  # word hyphenated across a line break: keep the hyphen, no space
        else:
            text += (" " if text else "") + line
    return re.sub(r"\s+", " ", text)


def parse_decision(pages: list[str]) -> ParsedDecision:
    lines = [line for page in pages for line in page.split("\n")]
    members_at = next(
        (i for i, line in enumerate(lines) if _MEMBERS_HEADER.match(line.strip())), None
    )
    if members_at is not None:
        end = members_at + 1
        while end < len(lines) and not lines[end].rstrip().endswith("."):
            end += 1
        header = _join_lines(lines[:members_at])
        members = _join_lines(lines[members_at + 1 : end + 1])
        body = lines[end + 1 :]
    else:  # older decisions: page number, title and date line, then the body
        header, members, body = _join_lines(lines[:3]), None, lines[3:]
    sentences = [s.strip() for s in _SENTENCE_END.split(_join_lines(body)) if s.strip()]
    return ParsedDecision(header=header, members=members, sentences=sentences)


def _terms(sentence: str, lexicon: tuple[str, ...]) -> list[str]:
    lowered = sentence.lower()
    return [term for term in lexicon if re.search(rf"\b{re.escape(term)}", lowered)]


def _score(sentence: str | None, language: Language) -> tuple[int, list[str], list[str]]:
    if not sentence:
        return 0, [], []
    hawkish = _terms(sentence, HAWKISH[language])
    dovish = _terms(sentence, DOVISH[language])
    return len(hawkish) - len(dovish), hawkish, dovish


def _word_changes(a: str, b: str) -> list[WordChange]:
    words_a, words_b = a.split(), b.split()
    changes: list[WordChange] = []
    matcher = difflib.SequenceMatcher(None, words_a, words_b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op in ("delete", "replace"):
            changes.append(WordChange(op="removed", text=" ".join(words_a[i1:i2])))
        if op in ("insert", "replace"):
            changes.append(WordChange(op="added", text=" ".join(words_b[j1:j2])))
    return changes


def _change(
    kind: ChangeKind, a: str | None, b: str | None, language: Language, similarity: float | None
) -> tuple[SentenceChange, int]:
    score_a, hawk_a, dove_a = _score(a, language)
    score_b, hawk_b, dove_b = _score(b, language)
    delta = score_b - score_a
    tone: Tone = "hardened" if delta > 0 else "softened" if delta < 0 else "no_change"
    return (
        SentenceChange(
            kind=kind,
            text_a=a,
            text_b=b,
            similarity=similarity,
            word_changes=_word_changes(a, b) if a and b else [],
            tone=tone,
            hawkish_terms=sorted(set(hawk_a) ^ set(hawk_b)) if a and b else hawk_a or hawk_b,
            dovish_terms=sorted(set(dove_a) ^ set(dove_b)) if a and b else dove_a or dove_b,
        ),
        delta,
    )


def diff_decisions(a: ParsedDecision, b: ParsedDecision, language: Language) -> DecisionDiff:
    changes: list[SentenceChange] = []
    unchanged = 0
    tone_score = 0

    def add(kind: ChangeKind, x: str | None, y: str | None, sim: float | None = None) -> None:
        nonlocal tone_score
        change, delta = _change(kind, x, y, language, sim)
        changes.append(change)
        tone_score += delta

    matcher = difflib.SequenceMatcher(None, a.sentences, b.sentences, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            unchanged += i2 - i1
            continue
        old, new = a.sentences[i1:i2], b.sentences[j1:j2]
        used: set[int] = set()
        for x in old:  # pair each removed sentence with its most similar added one
            best, best_ratio = None, 0.0
            for k, y in enumerate(new):
                if k in used:
                    continue
                ratio = difflib.SequenceMatcher(None, x, y, autojunk=False).ratio()
                if ratio > best_ratio:
                    best, best_ratio = k, ratio
            if best is not None and best_ratio >= MODIFIED_THRESHOLD:
                used.add(best)
                add("modified", x, new[best], round(best_ratio, 3))
            else:
                add("removed", x, None)
        for k, y in enumerate(new):
            if k not in used:
                add("added", None, y)
    return DecisionDiff(
        changes=changes,
        unchanged_count=unchanged,
        members_changed=(a.members or "") != (b.members or ""),
        tone_score=tone_score,
    )
