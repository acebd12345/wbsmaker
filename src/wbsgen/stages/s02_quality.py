"""Stage 02: Page quality classification — NORMAL_TEXT / IMAGE_ONLY / GARBLED_TEXT / MIXED / EMPTY."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import PageQuality, PageQualityResult, QualitySummary
from ..textutil import compute_text_stats


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pages_dir = proj_dir / "01_parse" / "pages"
    out_dir = proj_dir / "02_quality"
    out_dir.mkdir(parents=True, exist_ok=True)

    page_files = sorted(pages_dir.glob("p*.json"))
    results: list[PageQualityResult] = []

    # First pass: classify each page based on its own content
    for pf in page_files:
        data = json.loads(pf.read_text(encoding="utf-8"))
        page_idx = data["page_index"]
        blocks = data.get("blocks", [])

        full_text = "".join(b["text"] for b in blocks)
        stats = compute_text_stats(full_text)
        image_count = data.get("image_count", 0)

        quality = _classify_first_pass(
            char_count=stats["char_count"],
            cjk_count=stats["cjk_count"],
            image_count=image_count,
        )

        qr = PageQualityResult(
            page_index=page_idx,
            char_count=stats["char_count"],
            cjk_ratio=stats["cjk_ratio"],
            common_han_ratio=stats["common_han_ratio"],
            symbol_ratio=stats["symbol_ratio"],
            tounicode_ratio=0.0,
            quality=quality,
        )
        results.append(qr)

    # Second pass: resolve EMPTY and tentative IMAGE_ONLY using neighbor context
    _resolve_by_neighbors(results)

    # Compute summary
    summary = QualitySummary(total_pages=len(results))
    for qr in results:
        match qr.quality:
            case PageQuality.NORMAL_TEXT:
                summary.normal_text += 1
            case PageQuality.IMAGE_ONLY:
                summary.image_only += 1
            case PageQuality.GARBLED_TEXT:
                summary.garbled_text += 1
            case PageQuality.MIXED:
                summary.mixed += 1
            case PageQuality.EMPTY:
                summary.empty += 1

    # Write outputs
    with open(out_dir / "page_quality.jsonl", "w", encoding="utf-8") as f:
        for qr in results:
            f.write(qr.model_dump_json() + "\n")

    (out_dir / "summary.json").write_text(
        summary.model_dump_json(indent=2), encoding="utf-8"
    )
    return None


def _classify_first_pass(
    char_count: int,
    cjk_count: int,
    image_count: int,
) -> PageQuality:
    """Classify based on per-page content.

    Key insight for this document:
    - Normal pages have CJK characters (even TOC pages with dots have some CJK)
    - Garbled pages have ZERO CJK (subset font maps to wrong ASCII codepoints)
    - Blank pages need neighbor context (resolved in second pass)
    """
    if char_count == 0:
        if image_count > 0:
            return PageQuality.IMAGE_ONLY  # tentative; may become GARBLED in pass 2
        return PageQuality.EMPTY  # tentative; resolved in pass 2

    # Any CJK characters present → text is real (not garbled)
    if cjk_count > 0:
        return PageQuality.NORMAL_TEXT

    # Non-zero chars but zero CJK → garbled (wrong codepoints from subset fonts)
    return PageQuality.GARBLED_TEXT


def _resolve_by_neighbors(results: list[PageQualityResult]):
    """Second pass: resolve EMPTY and IMAGE_ONLY pages using neighbor context.

    - EMPTY pages surrounded by NORMAL → NORMAL_TEXT
    - EMPTY pages surrounded by GARBLED → GARBLED_TEXT
    - IMAGE_ONLY pages surrounded by GARBLED → GARBLED_TEXT
    """
    n = len(results)

    def _neighbor_quality(idx: int, direction: int, max_look: int = 5) -> PageQuality | None:
        """Find the nearest definitive (NORMAL or GARBLED) neighbor."""
        for offset in range(1, max_look + 1):
            ni = idx + direction * offset
            if 0 <= ni < n:
                q = results[ni].quality
                if q in (PageQuality.NORMAL_TEXT, PageQuality.GARBLED_TEXT):
                    return q
        return None

    for i, qr in enumerate(results):
        if qr.quality not in (PageQuality.EMPTY, PageQuality.IMAGE_ONLY):
            continue

        prev_q = _neighbor_quality(i, -1)
        next_q = _neighbor_quality(i, +1)

        if qr.quality == PageQuality.EMPTY:
            # Blank page: adopt neighbor's quality
            if prev_q == PageQuality.GARBLED_TEXT or next_q == PageQuality.GARBLED_TEXT:
                if prev_q == PageQuality.GARBLED_TEXT and next_q == PageQuality.GARBLED_TEXT:
                    qr.quality = PageQuality.GARBLED_TEXT
                elif prev_q == PageQuality.GARBLED_TEXT and next_q is None:
                    qr.quality = PageQuality.GARBLED_TEXT
                elif next_q == PageQuality.GARBLED_TEXT and prev_q is None:
                    qr.quality = PageQuality.GARBLED_TEXT
                else:
                    # On boundary: default to NORMAL
                    qr.quality = PageQuality.NORMAL_TEXT
            else:
                qr.quality = PageQuality.NORMAL_TEXT

        elif qr.quality == PageQuality.IMAGE_ONLY:
            # Image page adjacent to garbled content → GARBLED
            # (true scanned pages are clustered together, not adjacent to garbled)
            if prev_q == PageQuality.GARBLED_TEXT or next_q == PageQuality.GARBLED_TEXT:
                qr.quality = PageQuality.GARBLED_TEXT
            # Otherwise keep IMAGE_ONLY
