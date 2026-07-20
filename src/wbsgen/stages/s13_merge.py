"""Stage 13: Global WBS merge — LLM Task E + WBS code assignment."""
from __future__ import annotations

import json
from pathlib import Path

from ..llm.client import LLMClient
from ..models import GenerationType, WbsNode


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    localwbs_dir = proj_dir / "12_localwbs"
    out_dir = proj_dir / "13_merge"
    out_dir.mkdir(parents=True, exist_ok=True)

    client = LLMClient(cfg, proj_dir)
    prompt_path = Path(__file__).parent.parent / "llm" / "prompts" / "merge_global_wbs_v1.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Load all local WBS trees
    all_nodes: list[WbsNode] = []
    local_files = sorted(localwbs_dir.glob("*.json"))

    for lf in local_files:
        nodes = json.loads(lf.read_text(encoding="utf-8"))
        for nd in nodes:
            all_nodes.append(WbsNode(**nd))

    # Check for existing wbs.json with MANUAL/locked nodes
    existing_path = out_dir / "wbs.json"
    preserved: list[WbsNode] = []
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        for nd in existing:
            node = WbsNode(**nd)
            if node.generation_type == GenerationType.MANUAL or node.locked:
                preserved.append(node)

    # Create global root
    global_root = WbsNode(
        node_id="wbs-root",
        code="0",
        title="WBS Root",
        level=0,
        generation_type=GenerationType.INFERRED,
    )

    # Assign WBS codes to all nodes
    merged = [global_root]
    code_counter = 0

    for node in all_nodes:
        if node.level == 1:
            code_counter += 1
            node.code = str(code_counter)
            node.parent_id = "wbs-root"
            global_root.children.append(node.node_id)

            # Assign child codes
            child_counter = 0
            for child_node in all_nodes:
                if child_node.parent_id == node.node_id:
                    child_counter += 1
                    child_node.code = f"{code_counter}.{child_counter}"

        merged.append(node)

    # Re-add preserved MANUAL nodes
    for pn in preserved:
        if not any(n.node_id == pn.node_id for n in merged):
            merged.append(pn)

    # LLM merge call (mock mode)
    summaries = "\n".join(f"- {n.code} {n.title}" for n in merged if n.code)
    client.generate_json(
        system=system_prompt,
        user=summaries[:5000],
        schema={
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "title": {"type": "string"},
                            "level": {"type": "integer"},
                        },
                    },
                },
            },
        },
        temperature=cfg.get("llm", {}).get("temperature_wbs", 0.1),
    )

    # Write global WBS
    (out_dir / "wbs.json").write_text(
        json.dumps(
            [n.model_dump() for n in merged], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return None
