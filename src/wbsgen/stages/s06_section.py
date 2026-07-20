"""Stage 06: Section detection — locate chapter titles in body text and build sections."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import PageQuality, Section


# Chinese numerals for chapter headings
_CN_CHAPTER = ["壹", "貳", "參", "肆", "伍", "陸", "柒", "捌", "玖", "拾"]


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pages_dir = proj_dir / "01_parse" / "pages"
    subdocs_path = proj_dir / "04_subdoc" / "subdocs.json"
    toc_path = proj_dir / "05_toc" / "toc.json"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    out_dir = proj_dir / "06_section"
    out_dir.mkdir(parents=True, exist_ok=True)

    subdocs = json.loads(subdocs_path.read_text(encoding="utf-8"))
    toc_data = json.loads(toc_path.read_text(encoding="utf-8")) if toc_path.exists() else {}

    # Load quality to skip non-text pages
    qualities = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        qualities[q["page_index"]] = q["quality"]

    all_sections: list[Section] = []

    for subdoc in subdocs:
        sid = subdoc["subdoc_id"]
        doc_type = subdoc["doc_type"]
        start = subdoc["page_start"]
        end = subdoc["page_end"]

        # Only process document types that have meaningful sections
        if doc_type not in (
            "REQUIREMENT_SPECIFICATION", "CONTRACT_BODY",
            "BID_INSTRUCTIONS", "EVALUATION_GUIDELINES",
        ):
            continue

        # Get TOC entries for this subdoc
        toc_entries = toc_data.get(sid, [])

        # Load all blocks for this subdoc's pages
        page_blocks = {}
        for pi in range(start, end + 1):
            if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
                continue
            pf = pages_dir / f"p{pi+1:04d}.json"
            if pf.exists():
                data = json.loads(pf.read_text(encoding="utf-8"))
                page_blocks[pi] = data.get("blocks", [])

        # Find chapter titles in body text
        sections = _find_sections(sid, start, end, page_blocks, toc_entries, doc_type)
        all_sections.extend(sections)

    # Write output
    (out_dir / "sections.json").write_text(
        json.dumps(
            [s.model_dump() for s in all_sections], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return None


def _find_sections(
    subdoc_id: str,
    start: int,
    end: int,
    page_blocks: dict[int, list[dict]],
    toc_entries: list[dict],
    doc_type: str,
) -> list[Section]:
    """Find section boundaries using title blocks in body text."""
    # Build list of expected chapter titles from TOC
    expected_titles = []
    for entry in toc_entries:
        if entry.get("level", 1) == 1:
            expected_titles.append(entry["title"])

    # Scan all blocks for chapter title patterns
    title_hits: list[tuple[int, str, str, str]] = []  # (page_idx, block_id, title, numeral)

    for pi in sorted(page_blocks.keys()):
        for blk in page_blocks[pi]:
            # Skip blocks already marked as running header/footer
            if blk.get("role") in ("RUNNING_HEADER", "RUNNING_FOOTER", "PAGE_NUMBER"):
                continue
            if blk.get("exclude_from_content"):
                continue

            text = blk.get("text", "").strip()
            text_norm = re.sub(r"\s+", "", text)
            if not text_norm:
                continue

            # Check for chapter title pattern: 壹、title or 第一章 title
            hit = _match_chapter_title(text_norm, blk, doc_type)
            if hit:
                numeral, title = hit
                title_hits.append((pi, blk["block_id"], title, numeral))

    # If no TOC but we found titles, use them directly
    # If TOC exists, validate against it
    if not title_hits:
        return []

    # Build sections from consecutive title hits
    sections = []
    for i, (pi, bid, title, numeral) in enumerate(title_hits):
        # Section ends at the next title's page (or subdoc end)
        if i + 1 < len(title_hits):
            next_pi = title_hits[i + 1][0]
            next_bid = title_hits[i + 1][1]
            # If same page, end_page = same page
            if next_pi == pi:
                end_page = pi
            else:
                end_page = next_pi - 1
        else:
            end_page = end

        sections.append(Section(
            section_id=f"sec-{len(sections)+1:03d}",
            subdoc_id=subdoc_id,
            title=title,
            level=1,
            start_page=pi,
            end_page=end_page,
            start_block_id=bid,
            end_block_id=title_hits[i + 1][1] if i + 1 < len(title_hits) else "",
        ))

    return sections


def _match_chapter_title(text: str, blk: dict, doc_type: str) -> tuple[str, str] | None:
    """Match a block's text against chapter title patterns.

    Returns (numeral, full_title) if matched, None otherwise.
    Uses font size as additional signal — chapter titles are typically larger.
    """
    font_size = blk.get("font_size", 0)

    # For requirement specification: look for 壹、title pattern
    # Chapter titles typically have font_size >= 14
    if doc_type == "REQUIREMENT_SPECIFICATION":
        for cn in _CN_CHAPTER:
            pattern = rf"^({cn})[、.．,](.+)"
            m = re.match(pattern, text)
            if m and font_size >= 13:
                numeral = m.group(1)
                title_part = m.group(2).strip()
                # Filter out TOC entries (with dots)
                if "...." in blk.get("text", "") or "…" in blk.get("text", ""):
                    return None
                return numeral, f"{numeral}、{title_part}"

    # For contract body: look for 第N條 pattern
    if doc_type == "CONTRACT_BODY":
        m = re.match(r"^(第[一二三四五六七八九十百]+條)\s*(.+)", text)
        if m and font_size >= 13:
            return m.group(1), f"{m.group(1)} {m.group(2)}"

    # For bid instructions: look for numbered sections
    if doc_type == "BID_INSTRUCTIONS":
        m = re.match(r"^(第[一二三四五六七八九十百]+條|[一二三四五六七八九十]+[、.])\s*(.+)", text)
        if m and font_size >= 12:
            return m.group(1), f"{m.group(1)} {m.group(2)}"

    return None
