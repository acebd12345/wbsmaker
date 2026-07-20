"""Stage 11: Work item relationship building — LLM Task C."""
from __future__ import annotations

import json
from pathlib import Path

from ..llm.client import LLMClient
from ..models import Relation


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    items_path = proj_dir / "10_extract" / "work_items.jsonl"
    out_dir = proj_dir / "11_relate"
    out_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for line in items_path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            items.append(json.loads(line))

    client = LLMClient(cfg, proj_dir)
    prompt_path = Path(__file__).parent.parent / "llm" / "prompts" / "build_relations_v1.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    # Group items by section for manageable chunks
    section_items: dict[str, list[dict]] = {}
    for item in items:
        sid = item.get("section_id", "")
        section_items.setdefault(sid, []).append(item)

    all_relations: list[Relation] = []
    rel_counter = 0

    for sid, sec_items in section_items.items():
        if len(sec_items) < 2:
            continue

        # Build item summary for LLM
        summary = "\n".join(f"- {it['item_id']}: {it['description'][:100]}" for it in sec_items[:50])

        llm_result = client.generate_json(
            system=system_prompt,
            user=summary,
            schema={
                "type": "object",
                "properties": {
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "type": {"type": "string", "enum": ["depends_on", "part_of", "related_to", "precedes"]},
                            },
                        },
                    },
                },
            },
        )

        for rel_data in llm_result.get("relations", []):
            rel_counter += 1
            all_relations.append(Relation(
                relation_id=f"rel-{rel_counter:04d}",
                from_item=rel_data.get("from", ""),
                to_item=rel_data.get("to", ""),
                relation_type=rel_data.get("type", "related_to"),
            ))

    (out_dir / "relations.json").write_text(
        json.dumps(
            [r.model_dump() for r in all_relations], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return None
