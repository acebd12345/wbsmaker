"""P2 case-schema tests: round-trip, no-CJK fingerprint, runtime-id guard,
11108 label coverage, locators gitignored."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from wbsgen.cases import (
    Case, CaseSource, CaseStatus, Fingerprint, L1Label, L2Label, L3Label,
    L4Label, Labels, Provenance, load_case,
)

CASE_PATH = Path("cases/case-11108.json")
CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")


def _minimal_fingerprint() -> Fingerprint:
    return Fingerprint(
        total_pages=2, char_counts=[10, 20], image_counts=[0, 1],
        quality_sequence=["NORMAL_TEXT", "GARBLED_TEXT"],
        font_set_hash="a" * 64, family_pattern_hash="b" * 64,
    )


def _minimal_case(**over) -> Case:
    base = dict(
        case_id="case-x", pdf_sha256="deadbeef",
        source=CaseSource.GOLDEN, status=CaseStatus.ACTIVE,
        labels=Labels(L1=[L1Label(page_start=0, page_end=1, doc_type="CONTRACT_BODY")]),
        fingerprint=_minimal_fingerprint(),
        provenance=Provenance(annotator="x", annotated_at="2026-07-24"),
    )
    base.update(over)
    return Case(**base)


# ── orthogonality of source × status ───────────────────────────────────

def test_source_and_status_independent():
    c = _minimal_case(source=CaseSource.CORRECTED, status=CaseStatus.DRAFT)
    assert c.source == CaseSource.CORRECTED
    assert c.status == CaseStatus.DRAFT


# ── round-trip ─────────────────────────────────────────────────────────

def test_round_trip_dump_load():
    c = _minimal_case()
    dumped = json.dumps(c.model_dump(mode="json"), ensure_ascii=False)
    reloaded = Case.model_validate_json(dumped)
    assert reloaded.model_dump() == c.model_dump()


# ── runtime-id guard ───────────────────────────────────────────────────

def test_runtime_id_in_labels_rejected():
    with pytest.raises(ValueError):
        _minimal_case(labels=Labels(
            L2=[L2Label(page=3, title_norm="subdoc-004", doc_type="X")]
        ))


def test_block_id_in_labels_rejected():
    with pytest.raises(ValueError):
        _minimal_case(labels=Labels(
            L2=[L2Label(page=3, title_norm="p0072-b003", doc_type="X")]
        ))


# ── 11108 migrated case ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def case_11108() -> Case:
    assert CASE_PATH.exists(), "run scripts/migrate_case_11108.py first"
    return load_case(CASE_PATH)


def test_case_11108_validates(case_11108):
    assert case_11108.case_id == "case-11108"
    assert case_11108.source == CaseSource.GOLDEN
    assert case_11108.status == CaseStatus.ACTIVE
    assert case_11108.answer_schema_version == 1


def test_case_11108_label_coverage(case_11108):
    assert len(case_11108.labels.L1) == 10          # 10 subdocuments
    assert len(case_11108.labels.L2) == 7           # 7 requirement-spec chapters
    assert len(case_11108.labels.L3) == 4           # 4 excluded classes
    # L4 must at least cover 表5
    caps = [x.caption_norm for x in case_11108.labels.L4]
    assert any(re.search(r"表5", c) for c in caps), caps


def test_fingerprint_has_no_cjk(case_11108):
    blob = json.dumps(case_11108.fingerprint.model_dump(), ensure_ascii=False)
    found = CJK_RE.findall(blob)
    assert not found, f"fingerprint leaked CJK text: {found[:5]}"


def test_fingerprint_total_pages(case_11108):
    assert case_11108.fingerprint.total_pages == 309


# ── locators gitignored ────────────────────────────────────────────────

def test_locators_are_gitignored():
    ignore = Path(".gitignore").read_text(encoding="utf-8")
    assert "cases/locators.local.toml" in ignore
    check = subprocess.run(
        ["git", "check-ignore", "cases/locators.local.toml"],
        capture_output=True, text=True,
    )
    assert check.returncode == 0 and check.stdout.strip() == "cases/locators.local.toml"
