"""Stage 09: Content classification — rule-based + LLM Task A confirmation."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..llm.client import LLMClient
from ..models import ContentCategory, PageQuality

CATEGORY_ENUM = [c.value for c in ContentCategory]


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
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

    EXCLUDED_DOC_TYPES = {
        "BID_INSTRUCTIONS", "EVALUATION_GUIDELINES",
        "TENDER_ANNOUNCEMENT", "LAW_OR_POLICY",
    }

    classifications = []

    for sec in sections:
        subdoc = _find_subdoc(sec["subdoc_id"], subdocs)
        if subdoc and subdoc["doc_type"] in ("SERVICE_PROPOSAL", "SCANNED_PAGES"):
            continue

        # Rule-based classification
        category = _rule_classify(sec, tables)

        # Determine priority based on subdoc type
        priority = "PRIMARY"
        wbs_relevance = 0.8
        if subdoc and subdoc["doc_type"] in EXCLUDED_DOC_TYPES:
            priority = "EXCLUDED"
            wbs_relevance = 0.05
        elif subdoc and subdoc["doc_type"] == "REQUIREMENT_SPECIFICATION":
            priority = "PRIMARY"
            wbs_relevance = 0.95
        elif subdoc and subdoc["doc_type"] == "CONTRACT_BODY":
            priority = "SECONDARY"
            wbs_relevance = 0.6
        elif subdoc and subdoc["doc_type"] == "ATTACHMENT":
            # Round3-4: Attachments with payment/acceptance/delivery content are PRIMARY
            if category in (
                ContentCategory.DELIVERABLE.value, ContentCategory.QUALITY.value,
                ContentCategory.MILESTONE.value, ContentCategory.MAINTENANCE.value,
                ContentCategory.ACCEPTANCE.value, ContentCategory.PAYMENT.value,
                ContentCategory.TRAINING.value, ContentCategory.TRANSITION.value,
            ):
                priority = "PRIMARY"
                wbs_relevance = 0.90
            else:
                priority = "SECONDARY"
                wbs_relevance = 0.5

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


def _rule_classify(sec: dict, tables: list[dict]) -> str:
    """Rule-based classification using keywords and table captions."""
    title = sec.get("title", "")
    title_norm = re.sub(r"\s+", "", title)

    # Payment/acceptance patterns (Round3-4)
    if re.search(r"價金|給付|付款|計價|費用", title_norm):
        return ContentCategory.PAYMENT.value
    if re.search(r"驗收|查驗", title_norm):
        return ContentCategory.ACCEPTANCE.value
    if re.search(r"訓練|教育", title_norm):
        return ContentCategory.TRAINING.value
    if re.search(r"退場|移轉", title_norm):
        return ContentCategory.TRANSITION.value

    if re.search(r"目標|範圍|概述|背景", title_norm):
        return ContentCategory.SCOPE.value
    if re.search(r"維運|維護|保養|支援", title_norm):
        return ContentCategory.MAINTENANCE.value
    if re.search(r"交付|產出|文件|報告", title_norm):
        return ContentCategory.DELIVERABLE.value
    if re.search(r"時程|期限|里程碑", title_norm):
        return ContentCategory.MILESTONE.value
    if re.search(r"品質|檢核", title_norm):
        return ContentCategory.QUALITY.value
    if re.search(r"管理|組織|人力", title_norm):
        return ContentCategory.MANAGEMENT.value
    if re.search(r"限制|約束|條件", title_norm):
        return ContentCategory.CONSTRAINT.value
    if re.search(r"安全|資安", title_norm):
        return ContentCategory.SECURITY_ACTIVITY.value
    if re.search(r"功能|系統|新增|異動", title_norm):
        return ContentCategory.SCOPE.value

    return ContentCategory.UNCLASSIFIED.value
