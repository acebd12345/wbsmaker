"""Stage 07: Table detection, cross-page merging, and duplicate header removal."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber

from ..models import PageQuality, Table, TableFragment


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pdf_path = proj_dir / "original" / "contract.pdf"
    pages_dir = proj_dir / "01_parse" / "pages"
    sections_path = proj_dir / "06_section" / "sections.json"
    subdocs_path = proj_dir / "04_subdoc" / "subdocs.json"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    out_dir = proj_dir / "07_table"
    out_dir.mkdir(parents=True, exist_ok=True)

    tcfg = cfg.get("table", {})
    merge_conf_min = tcfg.get("merge_confidence_min", 0.80)

    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    subdocs = json.loads(subdocs_path.read_text(encoding="utf-8"))

    # Load quality
    qualities = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        qualities[q["page_index"]] = q["quality"]

    # Determine which pages to scan for tables (normal text pages in relevant subdocs)
    relevant_types = {
        "REQUIREMENT_SPECIFICATION", "CONTRACT_BODY", "ATTACHMENT",
        "BID_INSTRUCTIONS", "EVALUATION_GUIDELINES",
    }
    scan_pages = set()
    for sd in subdocs:
        if sd["doc_type"] in relevant_types:
            for pi in range(sd["page_start"], sd["page_end"] + 1):
                if qualities.get(pi) == PageQuality.NORMAL_TEXT.value:
                    scan_pages.add(pi)

    # Extract table fragments using pdfplumber
    fragments: list[TableFragment] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pi in sorted(scan_pages):
            if pi >= len(pdf.pages):
                continue
            page = pdf.pages[pi]
            tables = page.find_tables()
            for ti, tbl in enumerate(tables):
                rows = tbl.extract()
                if not rows:
                    continue

                # Clean rows
                clean_rows = []
                for row in rows:
                    clean_rows.append([cell.strip() if cell else "" for cell in row])

                header = clean_rows[0] if clean_rows else []
                col_sig = _column_signature(header)

                bbox = tbl.bbox  # (x0, y0, x1, y1)
                frag = TableFragment(
                    fragment_id=f"frag-p{pi+1:04d}-t{ti}",
                    page_index=pi,
                    bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                    rows=clean_rows,
                    header_row=header,
                    column_signature=col_sig,
                )
                fragments.append(frag)

    # Write fragments
    with open(out_dir / "fragments.jsonl", "w", encoding="utf-8") as f:
        for frag in fragments:
            f.write(frag.model_dump_json() + "\n")

    # Find table captions from text blocks
    captions: dict[int, str] = {}  # page_index -> caption text
    for pi in sorted(scan_pages):
        pf = pages_dir / f"p{pi+1:04d}.json"
        if not pf.exists():
            continue
        data = json.loads(pf.read_text(encoding="utf-8"))
        for blk in data.get("blocks", []):
            text = blk.get("text", "").strip()
            text_norm = re.sub(r"\s+", "", text)
            # Match "表N" or "表 N" caption pattern
            m = re.match(r"表\s*(\d+)\s+(.+)", text.strip())
            if not m:
                m = re.match(r"表(\d+)\s*(.+)", text_norm)
            if m:
                tbl_num = int(m.group(1))
                caption = f"表{tbl_num} {m.group(2)}"
                if pi not in captions:
                    captions[pi] = caption

    # Group fragments into tables by column signature + proximity
    tables = _merge_fragments(fragments, captions, merge_conf_min)

    # Assign sections to tables
    for tbl in tables:
        for sec in sections:
            if sec["start_page"] <= tbl.page_start <= sec["end_page"]:
                tbl.section_id = sec["section_id"]
                break

    # Write tables.json
    (out_dir / "tables.json").write_text(
        json.dumps(
            [t.model_dump() for t in tables], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return None


def _column_signature(header: list[str]) -> str:
    """Create a normalized column signature from header row."""
    return "|".join(re.sub(r"\s+", "", h) for h in header if h)


def _merge_fragments(
    fragments: list[TableFragment],
    captions: dict[int, str],
    merge_conf_min: float,
) -> list[Table]:
    """Merge table fragments across pages into logical tables."""
    if not fragments:
        return []

    # Group consecutive fragments with same column signature
    groups: list[list[TableFragment]] = []
    current_group: list[TableFragment] = [fragments[0]]

    for frag in fragments[1:]:
        prev = current_group[-1]
        same_sig = (frag.column_signature == prev.column_signature
                    and frag.column_signature)
        consecutive = (frag.page_index == prev.page_index + 1
                       or frag.page_index == prev.page_index)
        no_new_caption = frag.page_index not in captions

        if same_sig and consecutive and no_new_caption:
            current_group.append(frag)
        else:
            groups.append(current_group)
            current_group = [frag]

    groups.append(current_group)

    # Build tables from groups
    tables: list[Table] = []
    table_num = 0

    for group in groups:
        table_num += 1
        first = group[0]
        last = group[-1]

        # Find caption
        caption = ""
        for frag in group:
            if frag.page_index in captions:
                caption = captions[frag.page_index]
                break
        # Also check the page just before the first fragment
        if not caption and first.page_index - 1 in captions:
            caption = captions[first.page_index - 1]
        if not caption and first.page_index in captions:
            caption = captions[first.page_index]

        # Merge rows, removing duplicate headers
        header = first.header_row
        all_rows: list[list[str]] = []
        header_norm = [h.strip() for h in header]

        for frag in group:
            for row in frag.rows:
                row_norm = [c.strip() for c in row]
                # Skip if this row matches the header
                if row_norm == header_norm:
                    continue
                all_rows.append(row)

        cross_page = first.page_index != last.page_index
        confidence = 1.0 if len(group) == 1 else (
            0.95 if all(f.column_signature == first.column_signature for f in group) else 0.7
        )

        tbl = Table(
            table_id=f"tbl-{table_num:03d}",
            caption=caption,
            page_start=first.page_index,
            page_end=last.page_index,
            fragments=[f.fragment_id for f in group],
            header_row=header,
            rows=all_rows,
            cross_page_merged=cross_page,
            needs_review=confidence < merge_conf_min,
            merge_confidence=confidence,
        )
        tables.append(tbl)

    return tables
