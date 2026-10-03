import datetime as dt

from tr_macro_mcp.analysis.compare import diff_decisions, parse_decision
from tr_macro_mcp.analysis.explain import movement
from tr_macro_mcp.models import Observation

NEW_FORMAT = """1
Monetary Policy Committee Decision
July 23, 2026 No: 2026-28
Participating Committee Members
A Person (Governor), B Person,
C Person.
The Committee has decided to keep the policy rate (the one-
week repo auction rate) at 37 percent. Inflation decelerated in June.
The Committee remains attentive to upside risks."""


def test_parse_decision_splits_header_members_and_sentences() -> None:
    parsed = parse_decision([NEW_FORMAT])
    assert parsed.header.endswith("No: 2026-28")
    assert parsed.members == "A Person (Governor), B Person, C Person."
    assert parsed.sentences == [
        "The Committee has decided to keep the policy rate (the one-week repo auction rate) at "
        "37 percent.",
        "Inflation decelerated in June.",
        "The Committee remains attentive to upside risks.",
    ]


def test_parse_decision_without_members_list() -> None:
    old = "No: 2014-06\nRelease Date: 28 January 2014\nDECISION\nRates were raised. Risks rose."
    assert parse_decision([old]).sentences == ["Rates were raised.", "Risks rose."]


def test_diff_classifies_changes_and_tone() -> None:
    a = parse_decision([NEW_FORMAT])
    b_text = NEW_FORMAT.replace("Inflation decelerated in June.", "Inflation decelerated in July.")
    b_text = b_text.replace(
        "remains attentive to upside risks", "will tighten if risks materialise"
    )
    b_text += " A new sentence on reserves."
    diff = diff_decisions(a, parse_decision([b_text]), "en")
    kinds = [c.kind for c in diff.changes]
    assert kinds.count("modified") == 1
    month = next(c for c in diff.changes if c.kind == "modified")
    assert [(w.op, w.text) for w in month.word_changes] == [
        ("removed", "June."),
        ("added", "July."),
    ]
    assert "added" in kinds
    assert diff.unchanged_count == 1


def test_movement_summary() -> None:
    obs = [
        Observation(period=str(d), date=dt.date(2026, 7, d), value=v)
        for d, v in [(20, 40.0), (21, 41.0), (22, None), (23, 39.0), (24, 40.5)]
    ]
    moved = movement(obs)
    assert moved is not None
    assert (moved.first_value, moved.last_value, moved.change_abs) == (40.0, 40.5, 0.5)
    assert (moved.max_date, moved.min_date) == (dt.date(2026, 7, 21), dt.date(2026, 7, 23))
    assert moved.largest_moves[0].from_date == dt.date(2026, 7, 21)  # 41 -> 39 is the biggest
    assert movement(obs[:1]) is None
