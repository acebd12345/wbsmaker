"""Stage 09: Content classification — rule-based + LLM Task A confirmation."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..llm.client import LLMClient
from ..models import ContentCategory, PageQuality
from ..profile import ClassifyProfile, load_profile

CATEGORY_ENUM = [c.value for c in ContentCategory]


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    profile = load_profile(cfg)
    cls = profile.classify
    sections_path = proj_dir / "06_section" / "sections.json"
    tables_path = proj_dir / "07_table" / "tables.json"
    subdocs_path = proj_dir / "04_subdoc" / "subdocs.json"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    assemble_dir = proj_dir / "08_assemble"
    out_dir = proj_dir / "09_classify"
    out_dir.mkdir(parents=True, exist_ok=True)

    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    tables = json.loads(tables_path.read_text(encoding="utf-8"))
    subdocs = json.loads(subdocs_path.read_text(encoding="utf-8"))

    qualities = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        qualities[q["page_index"]] = q["quality"]

    client = LLMClient(cfg, proj_dir)
    prompt_path = Path(__file__).parent.parent / "llm" / "prompts" / "classify_content_v1.txt"
    system_prompt = prompt_path.read_text(encoding="utf-8")

    excluded_doc_types = set(cls.excluded_doc_types)
    skip_doc_types = set(cls.skip_doc_types)
    prio = cls.priority

    classifications = []

    for sec in sections:
        subdoc = _find_subdoc(sec["subdoc_id"], subdocs)
        if subdoc and subdoc["doc_type"] in skip_doc_types:
            continue

        # Rule-based classification
        category = _rule_classify(sec, tables, cls)

        # Determine priority based on subdoc type (profile-driven)
        priority = prio.default.priority
        wbs_relevance = prio.default.wbs_relevance
        dt = subdoc["doc_type"] if subdoc else None
        if dt in excluded_doc_types:
            priority = prio.excluded.priority
            wbs_relevance = prio.excluded.wbs_relevance
        elif dt == "REQUIREMENT_SPECIFICATION":
            priority = prio.REQUIREMENT_SPECIFICATION.priority
            wbs_relevance = prio.REQUIREMENT_SPECIFICATION.wbs_relevance
        elif dt == "CONTRACT_BODY":
            priority = prio.CONTRACT_BODY.priority
            wbs_relevance = prio.CONTRACT_BODY.wbs_relevance
        elif dt == "ATTACHMENT":
            # Attachments with payment/acceptance/delivery content are PRIMARY
            if category in prio.ATTACHMENT.primary_categories:
                priority = "PRIMARY"
                wbs_relevance = prio.ATTACHMENT.primary_relevance
            else:
                priority = "SECONDARY"
                wbs_relevance = prio.ATTACHMENT.secondary_relevance

        # Read assembled content
        md_path = assemble_dir / f"{sec['section_id']}.md"
        content = ""
        if md_path.exists():
            content = md_path.read_text(encoding="utf-8")

        has_garbled = any(
            qualities.get(pi) in (PageQuality.GARBLED_TEXT.value, PageQuality.IMAGE_ONLY.value)
            for pi in range(sec["start_page"], sec["end_page"] + 1)
        )

        # LLM confirmation for non-EXCLUDED sections
        final = category
        if content and not has_garbled and priority != "EXCLUDED":
            try:
                llm_result = client.generate_json(
                    system=system_prompt,
                    user=content[:2000],
                    schema={
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "enum": CATEGORY_ENUM},
                            "confidence": {"type": "number"},
                            "reasoning": {"type": "string"},
                        },
                    },
                )
                llm_cat = llm_result.get("category", category)
                if category == ContentCategory.UNCLASSIFIED.value:
                    final = llm_cat
                else:
                    final = category
            except RuntimeError:
                final = category

        classifications.append({
            "section_id": sec["section_id"],
            "subdoc_id": sec["subdoc_id"],
            "title": sec["title"],
            "category": final,
            "rule_category": category,
            "priority": priority,
            "wbs_relevance": wbs_relevance,
        })

    (out_dir / "classifications.json").write_text(
        json.dumps(classifications, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return None


def _find_subdoc(subdoc_id: str, subdocs: list[dict]) -> dict | None:
    for s in subdocs:
        if s["subdoc_id"] == subdoc_id:
            return s
    return None


def _rule_classify(sec: dict, tables: list[dict], cls: ClassifyProfile) -> str:
    """Rule-based classification using ordered title keyword rules (profile).

    Rule order is significant (e.g. security keywords must precede the
    function/system rule); the profile preserves that order.
    """
    title = sec.get("title", "")
    title_norm = re.sub(r"\s+", "", title)

    for rule in cls.title_rules:
        if re.search(rule.pattern, title_norm):
            return rule.category

    return ContentCategory.UNCLASSIFIED.value
