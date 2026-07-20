"""Stage 12: Local WBS generation — LLM Task D."""
from __future__ import annotations

import json
from pathlib import Path

from ..llm.client import LLMClient
from ..models import GenerationType, WbsNode


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    items_path = proj_dir / "10_extract" / "work_items.jsonl"
    classify_path = proj_dir / "09_classify" / "classifications.json"
    sections_path = proj_dir / "06_section" / "sections.json"
    subdocs_path = proj_dir / "04_subdoc" / "subdocs.json"
    out_dir = proj_dir / "12_localwbs"
    # Clean previous outputs to avoid stale files from earlier runs
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

    # Build priority map from classifications
    priority_map = {c["section_id"]: c.get("priority", "PRIMARY") for c in classifications}

    # Group items by subdoc for local WBS generation (skip EXCLUDED)
    subdoc_items: dict[str, list[dict]] = {}
    sec_to_subdoc = {s["section_id"]: s["subdoc_id"] for s in sections}

    for item in items:
        sec_id = item.get("section_id", "")
        if priority_map.get(sec_id, "PRIMARY") == "EXCLUDED":
            continue
        sid = sec_to_subdoc.get(sec_id, "unknown")
        subdoc_items.setdefault(sid, []).append(item)

    # Generate local WBS per subdoc
    node_counter = 0
    for subdoc_id, sd_items in subdoc_items.items():
        subdoc = next((s for s in subdocs if s["subdoc_id"] == subdoc_id), None)
        if not subdoc:
            continue

        # Group by section
        section_groups: dict[str, list[dict]] = {}
        for item in sd_items:
            section_groups.setdefault(item.get("section_id", ""), []).append(item)

        nodes: list[WbsNode] = []

        # Create a root node for this subdoc
        node_counter += 1
        root_id = f"wbs-{node_counter}"
        root = WbsNode(
            node_id=root_id,
            title=subdoc.get("title", subdoc_id),
            level=1,
            generation_type=GenerationType.EXPLICIT,
        )
        nodes.append(root)

        # Create child nodes per section
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

        # LLM call (mock mode generates synthetic)
        item_summary = "\n".join(f"- {it['item_id']}: {it['description'][:80]}" for it in sd_items[:50])
        client.generate_json(
            system=system_prompt,
            user=item_summary,
            schema={
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "level": {"type": "integer"},
                            },
                        },
                    },
                },
            },
            temperature=cfg.get("llm", {}).get("temperature_wbs", 0.1),
        )

        # Write local WBS
        (out_dir / f"{subdoc_id}.json").write_text(
            json.dumps(
                [n.model_dump() for n in nodes], ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )

    return None
