"""Stage 12: Local WBS generation — LLM Task D (no silent fallback)."""
from __future__ import annotations

import json
from pathlib import Path

from ..llm.client import LLMClient, chunk_text, DEFAULT_CHUNK_SIZE
from ..models import GenerationType, WbsNode

LOCALWBS_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "level": {"type": "integer"},
                    "children": {"type": "array", "items": {"type": "string"}},
                    "work_items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title"],
            },
        },
    },
    "required": ["nodes"],
}


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    items_path = proj_dir / "10_extract" / "work_items.jsonl"
    classify_path = proj_dir / "09_classify" / "classifications.json"
    sections_path = proj_dir / "06_section" / "sections.json"
    subdocs_path = proj_dir / "04_subdoc" / "subdocs.json"
    out_dir = proj_dir / "12_localwbs"
    if out_dir.exists():
        for old_file in out_dir.glob("*.json"):
            old_file.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for line in items_path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            items.append(json.loads(line))

    classifications = json.loads(classify_path.read_text(encoding="utf-8"))
    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    subdocs = json.loads(subdocs_path.read_text(encoding="utf-8"))

    client = LLMClient(cfg, proj_dir)
    prompt_path = Path(__file__).parent.parent / "llm" / "prompts" / "generate_local_wbs_v1.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    priority_map = {c["section_id"]: c.get("priority", "PRIMARY") for c in classifications}
    sec_to_subdoc = {s["section_id"]: s["subdoc_id"] for s in sections}

    # Group items by subdoc (skip EXCLUDED)
    subdoc_items: dict[str, list[dict]] = {}
    for item in items:
        sec_id = item.get("section_id", "")
        if priority_map.get(sec_id, "PRIMARY") == "EXCLUDED":
            continue
        sid = sec_to_subdoc.get(sec_id, item.get("subdoc_id", "unknown"))
        subdoc_items.setdefault(sid, []).append(item)

    node_counter = 0
    llm_failed_subdocs: list[str] = []

    for subdoc_id, sd_items in subdoc_items.items():
        subdoc = next((s for s in subdocs if s["subdoc_id"] == subdoc_id), None)
        if not subdoc:
            continue

        # Group by section
        section_groups: dict[str, list[dict]] = {}
        for item in sd_items:
            section_groups.setdefault(item.get("section_id", ""), []).append(item)

        # Build item summary for LLM
        item_summary_parts = []
        for sec_id, sec_items in section_groups.items():
            sec = next((s for s in sections if s["section_id"] == sec_id), None)
            sec_title = sec["title"] if sec else sec_id
            item_summary_parts.append(f"\n## {sec_title}")
            for it in sec_items:
                item_summary_parts.append(f"- [{it['item_id']}] {it['description'][:100]}")
        item_summary = "\n".join(item_summary_parts)

        # Try LLM for WBS structure
        llm_nodes = None
        chunks = chunk_text(item_summary, max_chars=DEFAULT_CHUNK_SIZE)
        for chunk in chunks:
            try:
                llm_result = client.generate_json(
                    system=system_prompt,
                    user=chunk,
                    schema=LOCALWBS_SCHEMA,
                    temperature=cfg.get("llm", {}).get("temperature_wbs", 0.1),
                )
                if llm_result.get("nodes"):
                    llm_nodes = llm_result["nodes"]
            except RuntimeError:
                llm_failed_subdocs.append(subdoc_id)

        # Build WBS from LLM response or structured rule-based
        nodes: list[WbsNode] = []
        node_counter += 1
        root_id = f"wbs-{node_counter}"
        root = WbsNode(
            node_id=root_id,
            title=subdoc.get("title", subdoc_id),
            level=1,
            generation_type=GenerationType.EXPLICIT,
        )
        nodes.append(root)

        # Detect mock data: if all titles are "mock_*", use rule-based instead
        if llm_nodes and all(
            n.get("title", "").startswith("mock_") for n in llm_nodes
        ):
            llm_nodes = None

        if llm_nodes:
            # Use LLM structure: create child nodes from LLM output
            for ln in llm_nodes:
                node_counter += 1
                child_id = f"wbs-{node_counter}"
                child = WbsNode(
                    node_id=child_id,
                    title=ln.get("title", ""),
                    level=2,
                    parent_id=root_id,
                    work_items=ln.get("work_items", []),
                    generation_type=GenerationType.INFERRED,
                )
                nodes.append(child)
                root.children.append(child_id)

                # Create level-3 children if present
                for sub_title in ln.get("children", []):
                    if isinstance(sub_title, str) and sub_title.strip():
                        node_counter += 1
                        sub_id = f"wbs-{node_counter}"
                        sub_node = WbsNode(
                            node_id=sub_id,
                            title=sub_title,
                            level=3,
                            parent_id=child_id,
                            generation_type=GenerationType.INFERRED,
                        )
                        nodes.append(sub_node)
                        child.children.append(sub_id)
        else:
            # Structured rule-based: group by section, attach work items
            for sec_id, sec_items in section_groups.items():
                sec = next((s for s in sections if s["section_id"] == sec_id), None)
                if not sec:
                    continue
                node_counter += 1
                sec_node_id = f"wbs-{node_counter}"
                sec_node = WbsNode(
                    node_id=sec_node_id,
                    title=sec.get("title", sec_id),
                    level=2,
                    parent_id=root_id,
                    work_items=[it["item_id"] for it in sec_items],
                    generation_type=GenerationType.EXPLICIT,
                    source_pages=list(range(sec["start_page"], sec["end_page"] + 1)),
                )
                nodes.append(sec_node)
                root.children.append(sec_node_id)

                # Create level-3 nodes from individual work items
                for it in sec_items[:20]:  # Limit to avoid explosion
                    node_counter += 1
                    wi_node_id = f"wbs-{node_counter}"
                    wi_node = WbsNode(
                        node_id=wi_node_id,
                        title=it["description"][:100],
                        level=3,
                        parent_id=sec_node_id,
                        work_items=[it["item_id"]],
                        generation_type=GenerationType.EXPLICIT,
                        source_pages=it.get("source_pages", []),
                    )
                    nodes.append(wi_node)
                    sec_node.children.append(wi_node_id)

        (out_dir / f"{subdoc_id}.json").write_text(
            json.dumps([n.model_dump() for n in nodes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if llm_failed_subdocs:
        (out_dir / "llm_failures.json").write_text(
            json.dumps(llm_failed_subdocs, ensure_ascii=False), encoding="utf-8"
        )

    return None
