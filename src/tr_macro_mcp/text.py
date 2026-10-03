"""Turkish-aware text helpers."""

import unicodedata

_TURKISH_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")


def fold(text: str) -> str:
    """Lowercase, Turkish-aware, accent-free form for matching ('DÖVİZ Kuru' -> 'doviz kuru')."""
    text = text.translate(_TURKISH_FOLD).lower()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
