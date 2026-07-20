"""Stage 05: TOC (Table of Contents) parsing — extract chapter entries from TOC pages."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import TocEntry


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pages_dir = proj_dir / "01_parse" / "pages"
    subdocs_path = proj_dir / "04_subdoc" / "subdocs.json"
    out_dir = proj_dir / "05_toc"
    out_dir.mkdir(parents=True, exist_ok=True)

    subdocs = json.loads(subdocs_path.read_text(encoding="utf-8"))

    all_tocs: dict[str, list[dict]] = {}

    for subdoc in subdocs:
        sid = subdoc["subdoc_id"]
        start = subdoc["page_start"]
        end = subdoc["page_end"]

        # Look for TOC pages in the first few pages of each subdoc
        toc_entries = []
        for pi in range(start, min(start + 8, end + 1)):
            pf = pages_dir / f"p{pi+1:04d}.json"
            if not pf.exists():
                continue
            data = json.loads(pf.read_text(encoding="utf-8"))
            blocks = data.get("blocks", [])

            # Check if this page has a TOC heading ("目次" or "目錄")
            has_toc_heading = False
            for b in blocks:
                text = re.sub(r"\s+", "", b.get("text", ""))
                if re.match(r"目[次錄]", text):
                    has_toc_heading = True
                    break

            if not has_toc_heading:
                continue

            # Parse TOC entries from this page
            for b in blocks:
                text = b.get("text", "").strip()
                if not text:
                    continue
                # Skip the "目次" heading itself
                if re.match(r"\s*目[次錄]\s*$", text):
                    continue

                entries = _parse_toc_line(text)
                toc_entries.extend(entries)

        if toc_entries:
            all_tocs[sid] = [e.model_dump() for e in toc_entries]

    (out_dir / "toc.json").write_text(
        json.dumps(all_tocs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return None


# Chinese numeral map for chapter numbers
_CN_NUMERALS = {
    "壹": 1, "貳": 2, "參": 3, "肆": 4,
    "伍": 5, "陸": 6, "柒": 7, "捌": 8,
    "玖": 9, "拾": 10, "一": 1, "二": 2,
    "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10,
}


def _parse_toc_line(text: str) -> list[TocEntry]:
    """Parse a TOC line like '壹、 專案目標 ......... 1' into a TocEntry."""
    entries = []

    # Pattern: Chinese numeral + 、 + title + dots + page number
    # e.g., "壹、 專案目標 ........................................................ 1"
    pattern = r"([壹貳參肆伍陸柒捌玖拾一二三四五六七八九十]+)[、.．]\s*(.+?)\s*[.…·]+\s*(\d+)\s*$"
    m = re.match(pattern, text.strip())
    if m:
        numeral = m.group(1)
        title = m.group(2).strip()
        page_num = int(m.group(3))
        level = 1 if numeral in _CN_NUMERALS and _CN_NUMERALS[numeral] <= 10 else 2

        entries.append(TocEntry(
            title=f"{numeral}、{title}",
            printed_page=page_num,
            level=level,
        ))
        return entries

    # Pattern for sub-entries: (一) title ... page or 1. title ... page
    sub_pattern = r"[（(]([一二三四五六七八九十\d]+)[）)]\s*(.+?)\s*[.…·]+\s*(\d+)\s*$"
    m = re.match(sub_pattern, text.strip())
    if m:
        title = m.group(2).strip()
        page_num = int(m.group(3))
        entries.append(TocEntry(
            title=title,
            printed_page=page_num,
            level=2,
        ))
        return entries

    return entries
