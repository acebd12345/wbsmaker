"""Stage 10: Work item extraction — LLM Task B with chunked input."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..llm.client import LLMClient, chunk_text, DEFAULT_CHUNK_SIZE, MIN_CHUNK_SIZE
from ..models import ContentCategory, PageQuality, WorkItem

CATEGORY_ENUM = [c.value for c in ContentCategory]

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "work_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORY_ENUM},
                    "source_text": {"type": "string"},
                },
                "required": ["title", "category", "source_text"],
            },
        },
    },
    "required": ["work_items"],
}


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

    priority_map = {c["section_id"]: c.get("priority", "PRIMARY") for c in classifications}
    all_items: list[WorkItem] = []
    item_counter = 0
    unextracted: list[dict] = []

    for sec in sections:
        subdoc = _find_subdoc(sec["subdoc_id"], subdocs)
        if subdoc and subdoc["doc_type"] in ("SERVICE_PROPOSAL", "SCANNED_PAGES"):
            continue

        priority = priority_map.get(sec["section_id"], "PRIMARY")
        if priority == "EXCLUDED":
            continue

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

        cls_info = {c["section_id"]: c for c in classifications}.get(sec["section_id"], {})
        category = cls_info.get("category", "UNCLASSIFIED")

        # ── Rule-based: one item per table row ──
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
                    description=desc[:300],
                    category=_safe_category(category),
                    source_pages=[tbl["page_start"]],
                    source_text=desc[:300],
                ))

        # ── LLM extraction: chunk content, never truncate ──
        chunks = chunk_text(content, max_chars=DEFAULT_CHUNK_SIZE)

        for chunk in chunks:
            try:
                llm_result = _call_with_retry(
                    client, system_prompt, chunk, EXTRACT_SCHEMA
                )
            except RuntimeError:
                unextracted.append({
                    "section_id": sec["section_id"],
                    "chunk_len": len(chunk),
                    "reason": "LLM_FAILED",
                })
                continue

            for wi in llm_result.get("work_items", []):
                item_counter += 1
                # LLM category overrides only if different from generic SCOPE
                llm_cat = wi.get("category", "")
                item_cat = category  # default: section classification
                if llm_cat and llm_cat != "SCOPE" and llm_cat != "UNCLASSIFIED":
                    item_cat = llm_cat  # LLM provided specific category
                all_items.append(WorkItem(
                    item_id=f"item-{item_counter:04d}",
                    subdoc_id=sec["subdoc_id"],
                    section_id=sec["section_id"],
                    description=wi.get("title", wi.get("description", ""))[:300],
                    category=_safe_category(item_cat),
                    source_pages=list(range(sec["start_page"], sec["end_page"] + 1)),
                    source_text=wi.get("source_text", "")[:500],
                ))

    # Write output
    with open(out_dir / "work_items.jsonl", "w", encoding="utf-8") as f:
        for item in all_items:
            f.write(item.model_dump_json() + "\n")

    if unextracted:
        (out_dir / "unextracted.json").write_text(
            json.dumps(unextracted, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return None


def _call_with_retry(
    client: LLMClient, system: str, user: str, schema: dict
) -> dict:
    """Call LLM; on failure, halve the input and retry each half.

    Raises RuntimeError only if chunk < MIN_CHUNK_SIZE and still fails.
    """
    try:
        return client.generate_json(system=system, user=user, schema=schema)
    except RuntimeError:
        if len(user) <= MIN_CHUNK_SIZE:
            raise
        mid = len(user) // 2
        # Find a paragraph break near midpoint
        nl = user.rfind("\n", mid - 200, mid + 200)
        if nl > 0:
            mid = nl
        results = {"work_items": []}
        for half in (user[:mid], user[mid:]):
            if not half.strip():
                continue
            try:
                r = _call_with_retry(client, system, half.strip(), schema)
                results["work_items"].extend(r.get("work_items", []))
            except RuntimeError:
                pass  # recorded in unextracted by caller
        return results


def _safe_category(cat: str) -> ContentCategory:
    try:
        return ContentCategory(cat)
    except ValueError:
        return ContentCategory.UNCLASSIFIED


def _find_subdoc(subdoc_id: str, subdocs: list[dict]) -> dict | None:
    for s in subdocs:
        if s["subdoc_id"] == subdoc_id:
            return s
    return None
