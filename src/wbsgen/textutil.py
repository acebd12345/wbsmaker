"""Text utilities: common Chinese character set, normalization, page number stripping."""
from __future__ import annotations

import re
import unicodedata

# Common Chinese characters: CJK Unified Ideographs that appear frequently in
# traditional Chinese government documents. We use the Big5 Level-1 frequently-used
# character range as a practical approximation: U+4E00-U+9FFF covers CJK Unified,
# but we specifically build a set of the ~4800 most common traditional Chinese chars.
# For simplicity, we use the full CJK Unified range and check if a character is
# in the commonly-used subset by frequency analysis.

# Big5 常用字 (Level 1): approximately 5401 characters
# Rather than embedding the full list, we use a heuristic:
# Characters in U+4E00-U+9FFF that are common in government/legal documents.
# We'll build the set dynamically from a known range.

_COMMON_HAN_CACHE: set[str] | None = None


def _build_common_han_set() -> set[str]:
    """Build set of common Han characters (Big5 Level 1 + common simplified).

    We use Python's unicodedata to check if characters are CJK,
    and filter by Big5 encoding as a proxy for "commonly used traditional Chinese".
    """
    result = set()
    for cp in range(0x4E00, 0x9FFF + 1):
        ch = chr(cp)
        try:
            ch.encode("big5")
            result.add(ch)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    # Also add common punctuation used in Chinese docs
    for cp in range(0x3000, 0x303F + 1):  # CJK Symbols and Punctuation
        result.add(chr(cp))
    for cp in range(0xFF01, 0xFF60 + 1):  # Fullwidth Forms
        result.add(chr(cp))
    return result


def common_han_set() -> set[str]:
    global _COMMON_HAN_CACHE
    if _COMMON_HAN_CACHE is None:
        _COMMON_HAN_CACHE = _build_common_han_set()
    return _COMMON_HAN_CACHE


def is_cjk(ch: str) -> bool:
    """Check if character is in CJK Unified Ideographs range."""
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0xF900 <= cp <= 0xFAFF


def compute_text_stats(text: str) -> dict:
    """Compute character statistics for quality classification."""
    if not text:
        return {
            "char_count": 0, "cjk_count": 0, "cjk_ratio": 0.0,
            "common_han_count": 0, "common_han_ratio": 0.0,
            "symbol_count": 0, "symbol_ratio": 0.0,
            "alnum_count": 0, "alnum_ratio": 0.0,
        }

    chars = [ch for ch in text if not ch.isspace()]
    total = len(chars)
    if total == 0:
        return {
            "char_count": 0, "cjk_count": 0, "cjk_ratio": 0.0,
            "common_han_count": 0, "common_han_ratio": 0.0,
            "symbol_count": 0, "symbol_ratio": 0.0,
            "alnum_count": 0, "alnum_ratio": 0.0,
        }

    han_set = common_han_set()
    cjk_count = 0
    common_han_count = 0
    alnum_count = 0
    symbol_count = 0

    for ch in chars:
        if is_cjk(ch):
            cjk_count += 1
        if ch in han_set:
            common_han_count += 1
        elif ch.isalnum():
            alnum_count += 1
        else:
            cat = unicodedata.category(ch)
            if cat.startswith("P") or cat.startswith("S") or cat == "Cc":
                symbol_count += 1

    return {
        "char_count": total,
        "cjk_count": cjk_count,
        "cjk_ratio": cjk_count / total,
        "common_han_count": common_han_count,
        "common_han_ratio": common_han_count / total,
        "symbol_count": symbol_count,
        "symbol_ratio": symbol_count / total,
        "alnum_count": alnum_count,
        "alnum_ratio": alnum_count / total,
    }


def normalize_heading(text: str) -> str:
    """Normalize a heading string for matching: strip whitespace, normalize numbers."""
    text = re.sub(r"\s+", "", text)
    text = text.strip()
    return text


def strip_page_number(text: str) -> tuple[str, str]:
    """Strip trailing page number from a header/footer line.

    Returns (text_without_number, number_part).
    """
    m = re.search(r"\s+(\d+)\s*$", text)
    if m:
        return text[:m.start()].strip(), m.group(1)
    return text.strip(), ""


def normalize_for_family(text: str) -> str:
    """Normalize text for family detection: replace digits with {n}, strip whitespace."""
    text = re.sub(r"\d+", "{n}", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
