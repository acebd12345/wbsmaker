"""Stage 10: Work item extraction — LLM Task B."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..llm.client import LLMClient
from ..models import ContentCategory, PageQuality, WorkItem


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    sections_path = proj_dir / "06_section" / "sections.json"
    tables_path = proj_dir / "07_table" / "tables.json"
    classify_path = proj_dir / "09_classify" / "classifications.json"
    subdocs_path = proj_dir / "04_subdoc" / "subdocs.json"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    assemble_dir = proj_dir / "08_assemble"
    out_dir = proj_dir / "10_extract"
    out_dir.mkdir(parents=True, exist_ok=True)

    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    tables = json.loads(tables_path.read_text(encoding="utf-8"))
    classifications = json.loads(classify_path.read_text(encoding="utf-8"))
    subdocs = json.loads(subdocs_path.read_text(encoding="utf-8"))

    qualities = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        qualities[q["page_index"]] = q["quality"]

    client = LLMClient(cfg, proj_dir)
    prompt_path = Path(__file__).parent.parent / "llm" / "prompts" / "extract_work_items_v1.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    class_map = {c["section_id"]: c for c in classifications}
    all_items: list[WorkItem] = []
    item_counter = 0

    # Build priority map: only extract from PRIMARY/SECONDARY sources
    priority_map = {c["section_id"]: c.get("priority", "PRIMARY") for c in classifications}

    for sec in sections:
        subdoc = _find_subdoc(sec["subdoc_id"], subdocs)
        if subdoc and subdoc["doc_type"] in ("SERVICE_PROPOSAL", "SCANNED_PAGES"):
            continue

        # Skip EXCLUDED sections (Fix 3: priority filter)
        priority = priority_map.get(sec["section_id"], "PRIMARY")
        if priority == "EXCLUDED":
            continue

        # Check no garbled pages
        has_garbled = any(
            qualities.get(pi) in (PageQuality.GARBLED_TEXT.value, PageQuality.IMAGE_ONLY.value)
            for pi in range(sec["start_page"], sec["end_page"] + 1)
        )
        if has_garbled:
            continue

        md_path = assemble_dir / f"{sec['section_id']}.md"
        if not md_path.exists():
            continue
        content = md_path.read_text(encoding="utf-8")
        if not content.strip():
            continue

        cls_info = class_map.get(sec["section_id"], {})
        category = cls_info.get("category", "UNCLASSIFIED")

        # Extract first meaningful paragraph as source_text (not the title)
        lines = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("#")]
        first_paragraph = lines[0][:500] if lines else sec["title"]

        # Extract from text blocks
        item_counter += 1
        text_item = WorkItem(
            item_id=f"item-{item_counter:04d}",
            subdoc_id=sec["subdoc_id"],
            section_id=sec["section_id"],
            description=sec["title"],
            category=ContentCategory(category) if category in ContentCategory.__members__ else ContentCategory.UNCLASSIFIED,
            source_pages=list(range(sec["start_page"], sec["end_page"] + 1)),
            source_text=first_paragraph,
        )
        all_items.append(text_item)

        # Extract from tables in this section
        sec_tables = [t for t in tables if t.get("section_id") == sec["section_id"]]
        for tbl in sec_tables:
            for ri, row in enumerate(tbl.get("rows", [])):
                item_counter += 1
                desc = " | ".join(c for c in row if c)
                all_items.append(WorkItem(
                    item_id=f"item-{item_counter:04d}",
                    subdoc_id=sec["subdoc_id"],
                    section_id=sec["section_id"],
                    table_id=tbl["table_id"],
                    description=desc[:200],
                    category=ContentCategory(category) if category in ContentCategory.__members__ else ContentCategory.UNCLASSIFIED,
                    source_pages=[tbl["page_start"]],
                    source_text=desc[:200],
                ))

        # LLM extraction (mock mode)
        llm_result = client.generate_json(
            system=system_prompt,
            user=content[:5000],
            schema={
                "type": "object",
                "properties": {
                    "work_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "category": {"type": "string"},
                                "source_text": {"type": "string"},
                            },
                        },
                    },
                },
            },
        )

    # Write output
    with open(out_dir / "work_items.jsonl", "w", encoding="utf-8") as f:
        for item in all_items:
            f.write(item.model_dump_json() + "\n")

    return None


def _find_subdoc(subdoc_id: str, subdocs: list[dict]) -> dict | None:
    for s in subdocs:
        if s["subdoc_id"] == subdoc_id:
            return s
    return None
