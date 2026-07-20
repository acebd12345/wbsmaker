"""Stage 08: Assemble — organize section content into markdown for LLM input."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import PageQuality


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pages_dir = proj_dir / "01_parse" / "pages"
    sections_path = proj_dir / "06_section" / "sections.json"
    tables_path = proj_dir / "07_table" / "tables.json"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    out_dir = proj_dir / "08_assemble"
    out_dir.mkdir(parents=True, exist_ok=True)

    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    tables = json.loads(tables_path.read_text(encoding="utf-8"))
    qualities = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        qualities[q["page_index"]] = q["quality"]

    # Build table lookup by page range
    page_tables: dict[int, list[dict]] = {}
    for tbl in tables:
        for pi in range(tbl["page_start"], tbl["page_end"] + 1):
            page_tables.setdefault(pi, []).append(tbl)

    for sec in sections:
        sid = sec["section_id"]
        start = sec["start_page"]
        end = sec["end_page"]
        title = sec["title"]

        md_parts = [f"# {title}\n"]
        tables_rendered = set()

        for pi in range(start, end + 1):
            if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
                continue

            pf = pages_dir / f"p{pi+1:04d}.json"
            if not pf.exists():
                continue

            data = json.loads(pf.read_text(encoding="utf-8"))
            blocks = data.get("blocks", [])

            for blk in blocks:
                if blk.get("exclude_from_content"):
                    continue
                if blk.get("role") in ("RUNNING_HEADER", "RUNNING_FOOTER", "PAGE_NUMBER"):
                    continue

                text = blk.get("text", "").strip()
                if not text:
                    continue

                # Check if this block is a table caption
                caption_match = re.match(r"表\s*\d+", re.sub(r"\s+", "", text))
                if caption_match:
                    # Find and render the corresponding table
                    for tbl in page_tables.get(pi, []):
                        if tbl["table_id"] not in tables_rendered:
                            md_parts.append(f"\n{text}\n")
                            md_parts.append(_table_to_markdown(tbl))
                            tables_rendered.add(tbl["table_id"])
                    continue

                md_parts.append(text + "\n")

            # Check for tables on this page not yet rendered (no caption match)
            for tbl in page_tables.get(pi, []):
                if tbl["table_id"] not in tables_rendered:
                    if tbl["page_start"] == pi:  # Only render at first page
                        caption = tbl.get("caption", "")
                        if caption:
                            md_parts.append(f"\n{caption}\n")
                        md_parts.append(_table_to_markdown(tbl))
                        tables_rendered.add(tbl["table_id"])

        md_content = "\n".join(md_parts)
        (out_dir / f"{sid}.md").write_text(md_content, encoding="utf-8")

    return None


def _table_to_markdown(tbl: dict) -> str:
    """Convert a table to markdown format."""
    header = tbl.get("header_row", [])
    rows = tbl.get("rows", [])

    if not header:
        return ""

    lines = []
    # Header
    lines.append("| " + " | ".join(h or "" for h in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    # Data rows
    for row in rows:
        # Pad or truncate row to match header length
        padded = list(row) + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(c or "" for c in padded[:len(header)]) + " |")

    return "\n".join(lines) + "\n"
