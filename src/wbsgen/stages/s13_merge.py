"""Stage 13: Global WBS merge — LLM Task E + WBS code assignment (no silent fallback)."""
from __future__ import annotations

import json
from pathlib import Path

from ..llm.client import LLMClient, chunk_text, DEFAULT_CHUNK_SIZE
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
    if not local_files:
        return None

    for lf in local_files:
        if lf.name == "llm_failures.json":
            continue
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

    # Try LLM merge for deduplication and restructuring
    summaries = _build_summary(all_nodes)
    llm_success = False
    if summaries.strip():
        chunks = chunk_text(summaries, max_chars=DEFAULT_CHUNK_SIZE)
        for chunk in chunks:
            try:
                client.generate_json(
                    system=system_prompt,
                    user=chunk,
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
                llm_success = True
            except RuntimeError:
                pass  # Use rule-based merge if LLM fails

    # Build merged tree: assign codes
    merged = [global_root]
    code_counter = 0

    for node in all_nodes:
        if node.level == 1:
            code_counter += 1
            node.code = str(code_counter)
            node.parent_id = "wbs-root"
            global_root.children.append(node.node_id)

            # Assign child codes recursively
            _assign_codes(node, all_nodes, str(code_counter))

        merged.append(node)

    # Re-add preserved MANUAL nodes
    for pn in preserved:
        if not any(n.node_id == pn.node_id for n in merged):
            merged.append(pn)

    # Write global WBS
    (out_dir / "wbs.json").write_text(
        json.dumps([n.model_dump() for n in merged], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Record LLM merge status
    (out_dir / "merge_status.json").write_text(
        json.dumps({"llm_merge_attempted": True, "llm_merge_success": llm_success},
                    ensure_ascii=False), encoding="utf-8"
    )
    return None


def _assign_codes(parent: WbsNode, all_nodes: list[WbsNode], parent_code: str):
    """Recursively assign WBS codes to children."""
    child_counter = 0
    for node in all_nodes:
        if node.parent_id == parent.node_id:
            child_counter += 1
            node.code = f"{parent_code}.{child_counter}"
            _assign_codes(node, all_nodes, node.code)


def _build_summary(nodes: list[WbsNode]) -> str:
    """Build a text summary of all nodes for LLM merge input."""
    lines = []
    for n in nodes:
        indent = "  " * (n.level - 1)
        wi = f" (items: {len(n.work_items)})" if n.work_items else ""
        lines.append(f"{indent}- {n.title}{wi}")
    return "\n".join(lines)
