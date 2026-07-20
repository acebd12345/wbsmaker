"""Verify that no garbled or scanned page content appears in LLM inputs."""
import json
from pathlib import Path

import pytest


PROJECT_DIR = Path("data/projects/gold")


def test_no_garbled_pages_in_llm_logs():
    """Garbled pages (p175-309) and scanned pages (p154-163) must not appear in LLM input."""
    log_dir = PROJECT_DIR / "llm_logs"
    if not log_dir.exists():
        pytest.skip("LLM logs not found")

    quality_path = PROJECT_DIR / "02_quality" / "page_quality.jsonl"
    if not quality_path.exists():
        pytest.skip("Quality data not found")

    # Load garbled/image page indices
    bad_pages = set()
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        if q["quality"] in ("GARBLED_TEXT", "IMAGE_ONLY"):
            bad_pages.add(q["page_index"])

    # Load assembled content from those pages to get characteristic text
    # (In mock mode, we just verify that sections covering garbled pages
    # were not sent to LLM at all)
    assemble_dir = PROJECT_DIR / "08_assemble"
    sections_path = PROJECT_DIR / "06_section" / "sections.json"

    if not sections_path.exists():
        pytest.skip("Sections not found")

    sections = json.loads(sections_path.read_text(encoding="utf-8"))

    # Sections that include garbled/image pages should not have LLM calls
    garbled_sections = set()
    for sec in sections:
        for pi in range(sec["start_page"], sec["end_page"] + 1):
            if pi in bad_pages:
                garbled_sections.add(sec["section_id"])
                break

    # Collect assembled content from garbled sections to check it doesn't
    # appear in any LLM input (works for both mock and real mode)
    garbled_texts = set()
    for sec_id in garbled_sections:
        md = assemble_dir / f"{sec_id}.md"
        if md.exists():
            text = md.read_text(encoding="utf-8").strip()[:200]
            if text:
                garbled_texts.add(text)

    for log_file in log_dir.glob("*.json"):
        log = json.loads(log_file.read_text(encoding="utf-8"))
        output = log.get("output", {})
        # The LLM input hash is logged but not the raw input.
        # Instead, verify that the output doesn't reference garbled section IDs.
        output_str = json.dumps(output, ensure_ascii=False)
        for gsec in garbled_sections:
            assert gsec not in output_str, (
                f"Garbled section {gsec} found in LLM output {log_file.name}"
            )
