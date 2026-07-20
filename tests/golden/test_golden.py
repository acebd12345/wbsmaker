"""Golden tests: verify pipeline output against expected.json ground truth."""
from __future__ import annotations

import json
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

    def test_table_count(self):
        tables = self._load_tables()
        assert len(tables) == EXPECTED["tables"]["count"]

    def test_table5_cross_page(self):
        tables = self._load_tables()
        t5 = [t for t in tables if "5" in t.get("caption", "") or t.get("table_id", "").endswith("5")]
        assert len(t5) >= 1
        t = t5[0]
        exp = EXPECTED["tables"]["table5"]
        assert t["page_start"] == exp["page_start"]
        assert t["page_end"] == exp["page_end"]
        assert t["cross_page_merged"] is True

    def test_table5_no_duplicate_headers(self):
        tables = self._load_tables()
        t5 = [t for t in tables if "5" in t.get("caption", "") or t.get("table_id", "").endswith("5")]
        assert len(t5) >= 1
        t = t5[0]
        header = t.get("header_row", [])
        rows = t.get("rows", [])
        # Header row should not appear in data rows
        header_norm = [c.strip() for c in header]
        dup_count = sum(1 for r in rows if [c.strip() for c in r] == header_norm)
        assert dup_count == 0, f"Found {dup_count} duplicate header rows in table 5"
