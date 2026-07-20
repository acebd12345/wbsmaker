"""Golden tests: verify pipeline output against expected.json ground truth."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent
EXPECTED = json.loads((GOLDEN_DIR / "expected.json").read_text(encoding="utf-8"))
PROJECT_DIR = Path("data/projects/gold")


def _require_stage(stage_num: int, dirname: str):
    d = PROJECT_DIR / dirname
    if not d.exists():
        pytest.skip(f"Stage {stage_num} output not found: {dirname}")


# ── Step 2: Page counts ───────────────────────────────────────────────

class TestPageQuality:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(1, "01_parse")
        _require_stage(2, "02_quality")

    def _load_summary(self):
        return json.loads((PROJECT_DIR / "02_quality" / "summary.json").read_text(encoding="utf-8"))

    def _load_quality(self):
        lines = (PROJECT_DIR / "02_quality" / "page_quality.jsonl").read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(l) for l in lines]

    def test_total_pages(self):
        s = self._load_summary()
        assert s["total_pages"] == EXPECTED["total_pages"], f"Expected {EXPECTED['total_pages']}, got {s['total_pages']}"

    def test_normal_text_count(self):
        s = self._load_summary()
        assert s["normal_text"] == EXPECTED["quality"]["NORMAL_TEXT"]

    def test_image_only_count(self):
        s = self._load_summary()
        assert s["image_only"] == EXPECTED["quality"]["IMAGE_ONLY"]

    def test_garbled_text_count(self):
        s = self._load_summary()
        assert s["garbled_text"] == EXPECTED["quality"]["GARBLED_TEXT"]

    def test_image_only_pages(self):
        """IMAGE_ONLY pages should be exactly p154-163 (0-indexed: 153-162)."""
        quality = self._load_quality()
        img_pages = [q["page_index"] for q in quality if q["quality"] == "IMAGE_ONLY"]
        assert img_pages == EXPECTED["image_only_pages"]

    def test_garbled_pages_range(self):
        """GARBLED_TEXT should cover p175-309 (0-indexed: 174-308)."""
        quality = self._load_quality()
        garbled = [q["page_index"] for q in quality if q["quality"] == "GARBLED_TEXT"]
        assert min(garbled) == EXPECTED["garbled_pages_start"]
        assert max(garbled) == EXPECTED["garbled_pages_end"]
        assert len(garbled) == EXPECTED["quality"]["GARBLED_TEXT"]


# ── Step 3: Layout & Subdoc (to be enabled) ────────────────────────────

class TestSubdocuments:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(4, "04_subdoc")

    def _load_subdocs(self):
        return json.loads((PROJECT_DIR / "04_subdoc" / "subdocs.json").read_text(encoding="utf-8"))

    def test_subdoc_count(self):
        subdocs = self._load_subdocs()
        assert len(subdocs) == len(EXPECTED["subdoc_boundaries"])

    def test_requirement_spec_range(self):
        subdocs = self._load_subdocs()
        req = [s for s in subdocs if s["doc_type"] == "REQUIREMENT_SPECIFICATION"]
        assert len(req) == 1
        r = req[0]
        exp = EXPECTED["requirement_spec"]
        assert r["page_start"] == exp["subdoc_page_start"]
        assert r["page_end"] == exp["subdoc_page_end"]

    def test_cut_points(self):
        subdocs = self._load_subdocs()
        cuts = set()
        for s in subdocs:
            cuts.add(s["page_start"])
            cuts.add(s["page_end"])
        for cp in EXPECTED["subdoc_cut_points"]:
            assert cp in cuts, f"Missing cut point: {cp}"


class TestRunningHeaders:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(3, "03_layout")

    def _load_families(self):
        return json.loads((PROJECT_DIR / "03_layout" / "families.json").read_text(encoding="utf-8"))

    def test_has_running_headers(self):
        families = self._load_families()
        headers = [f for f in families if f["role"] == "RUNNING_HEADER"]
        assert len(headers) > 0


# ── Step 4: Sections ──────────────────────────────────────────────────

class TestSections:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(6, "06_section")

    def _load_sections(self):
        return json.loads((PROJECT_DIR / "06_section" / "sections.json").read_text(encoding="utf-8"))

    def _req_spec_subdoc_ids(self):
        """Find subdoc IDs that are REQUIREMENT_SPECIFICATION."""
        subdocs = json.loads((PROJECT_DIR / "04_subdoc" / "subdocs.json").read_text(encoding="utf-8"))
        return {s["subdoc_id"] for s in subdocs if s["doc_type"] == "REQUIREMENT_SPECIFICATION"}

    def test_chapter_count(self):
        sections = self._load_sections()
        req_ids = self._req_spec_subdoc_ids()
        req_sections = [s for s in sections if s.get("subdoc_id") in req_ids]
        top = [s for s in req_sections if s.get("level", 1) == 1]
        assert len(top) == EXPECTED["requirement_spec"]["chapter_count"]

    def test_chapter_1_2_same_page(self):
        sections = self._load_sections()
        req_ids = self._req_spec_subdoc_ids()
        req_sections = [s for s in sections if s.get("subdoc_id") in req_ids]
        top = sorted([s for s in req_sections if s.get("level", 1) == 1], key=lambda s: s["start_page"])
        assert len(top) >= 2
        assert top[0]["start_page"] == top[1]["start_page"] == EXPECTED["requirement_spec"]["chapter_1_2_same_page"]


# ── Step 5: Tables ────────────────────────────────────────────────────

class TestTables:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(7, "07_table")

    def _load_tables(self):
        return json.loads((PROJECT_DIR / "07_table" / "tables.json").read_text(encoding="utf-8"))

    def _captioned_tables(self):
        """Return tables with '表N' captions (the 10 named tables in req spec)."""
        import re
        tables = self._load_tables()
        return [t for t in tables if re.search(r"表\s*\d+", t.get("caption", ""))]

    def test_table_count(self):
        captioned = self._captioned_tables()
        assert len(captioned) == EXPECTED["tables"]["count"]

    def test_table5_cross_page(self):
        captioned = self._captioned_tables()
        t5 = [t for t in captioned if re.search(r"表\s*5\s", t.get("caption", ""))]
        assert len(t5) >= 1, f"Table 5 not found in {[t['caption'][:10] for t in captioned]}"
        t = t5[0]
        exp = EXPECTED["tables"]["table5"]
        assert t["page_start"] == exp["page_start"]
        assert t["page_end"] == exp["page_end"]
        assert t["cross_page_merged"] is True

    def test_table5_no_duplicate_headers(self):
        captioned = self._captioned_tables()
        t5 = [t for t in captioned if re.search(r"表\s*5\s", t.get("caption", ""))]
        assert len(t5) >= 1
        t = t5[0]
        header = t.get("header_row", [])
        rows = t.get("rows", [])
        header_norm = [c.strip() for c in header]
        dup_count = sum(1 for r in rows if [c.strip() for c in r] == header_norm)
        assert dup_count == 0, f"Found {dup_count} duplicate header rows in table 5"


# ── Fix 1: Section IDs globally unique + traceability ────────────────

class TestSectionIdUniqueness:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(6, "06_section")

    def _load_sections(self):
        return json.loads((PROJECT_DIR / "06_section" / "sections.json").read_text(encoding="utf-8"))

    def test_all_section_ids_globally_unique(self):
        sections = self._load_sections()
        ids = [s["section_id"] for s in sections]
        assert len(ids) == len(set(ids)), f"Duplicate section_ids found: {len(ids)} total, {len(set(ids))} unique"

    def test_section_id_contains_subdoc_prefix(self):
        """Every section_id should embed its subdoc_id for traceability."""
        sections = self._load_sections()
        for s in sections:
            assert s["section_id"].startswith(s["subdoc_id"]), (
                f"{s['section_id']} does not start with {s['subdoc_id']}"
            )

    def test_work_item_section_ids_resolve(self):
        """Every work item's section_id must exist in sections.json."""
        _require_stage(10, "10_extract")
        sections = self._load_sections()
        sec_ids = {s["section_id"] for s in sections}
        items_path = PROJECT_DIR / "10_extract" / "work_items.jsonl"
        for line in items_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            item = json.loads(line)
            assert item["section_id"] in sec_ids, (
                f"item {item['item_id']} references unknown section {item['section_id']}"
            )


# ── Fix 2: Contract body clean titles ────────────────────────────────

class TestContractBodyTitles:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(6, "06_section")

    def _load_sections(self):
        return json.loads((PROJECT_DIR / "06_section" / "sections.json").read_text(encoding="utf-8"))

    def test_no_toc_dots_in_titles(self):
        """subdoc-001 section titles must not contain TOC dot leaders."""
        sections = self._load_sections()
        s001 = [s for s in sections if s.get("subdoc_id") == "subdoc-001"]
        for s in s001:
            assert "...." not in s["title"], f"TOC dots in title: {s['title']}"
            assert "…" not in s["title"], f"Ellipsis in title: {s['title']}"

    def test_contract_body_anchors_in_body_range(self):
        """Contract body sections should anchor in body text (p2-47), not TOC (p0-1)."""
        sections = self._load_sections()
        exp = EXPECTED["sections_fix"]
        s001 = [s for s in sections if s.get("subdoc_id") == "subdoc-001"]
        assert len(s001) == exp["contract_body_section_count"]
        lo, hi = exp["contract_body_page_range"]
        for s in s001:
            assert lo <= s["start_page"] <= hi, (
                f"{s['section_id']} start_page {s['start_page']} outside [{lo}, {hi}]"
            )


# ── Fix 3: Priority filtering — EXCLUDED content out of WBS ─────────

class TestPriorityFiltering:
    @pytest.fixture(autouse=True)
    def setup(self):
        _require_stage(10, "10_extract")
        _require_stage(13, "13_merge")

    def _load_wbs(self):
        return json.loads((PROJECT_DIR / "13_merge" / "wbs.json").read_text(encoding="utf-8"))

    def _load_items(self):
        lines = (PROJECT_DIR / "10_extract" / "work_items.jsonl").read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(l) for l in lines if l.strip()]

    def test_wbs_level1_no_excluded(self):
        """WBS level 1 must not contain excluded document types."""
        wbs = self._load_wbs()
        l1_titles = [n["title"] for n in wbs if n["level"] == 1]
        for forbidden in EXPECTED["wbs_fix"]["level1_forbidden"]:
            assert forbidden not in l1_titles, f"Excluded '{forbidden}' found at WBS level 1"

    def test_wbs_has_required_branches(self):
        """WBS must contain maintenance/training/exit branches."""
        wbs = self._load_wbs()
        all_titles = " ".join(n["title"] for n in wbs)
        for kw in EXPECTED["wbs_fix"]["required_keywords"]:
            assert kw in all_titles, f"Required keyword '{kw}' not found in WBS"

    def test_work_items_only_from_allowed_subdocs(self):
        """Extracted work items should only come from PRIMARY/SECONDARY subdocs."""
        items = self._load_items()
        allowed = set(EXPECTED["wbs_fix"]["work_item_allowed_subdocs"])
        for it in items:
            assert it["subdoc_id"] in allowed, (
                f"item {it['item_id']} from excluded subdoc {it['subdoc_id']}"
            )

    def test_classifications_have_priority(self):
        """All classifications must have a priority field."""
        _require_stage(9, "09_classify")
        classifications = json.loads(
            (PROJECT_DIR / "09_classify" / "classifications.json").read_text(encoding="utf-8")
        )
        valid = {"PRIMARY", "SECONDARY", "EXCLUDED"}
        for c in classifications:
            assert "priority" in c, f"Missing priority for {c.get('section_id')}"
            assert c["priority"] in valid, f"Invalid priority {c['priority']}"
