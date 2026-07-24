"""P3 eval tests: scorecard schema, SKIPPED / FAIL semantics, synthetic run.

The full-library scorecard (case-11108 L1 F1 = 1.0) is a workstation command,
not part of the suite — here we only exercise the runner's control flow on a
tiny synthetic PDF plus the SKIP / FAIL branches and Pydantic validation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wbsgen.cases import (
    Case, CaseSource, CaseStatus, Fingerprint, Labels, Provenance, dump_case,
)
from wbsgen.eval import (
    Aggregates, ByUnit, FrozenScores, MergeGate, PerCaseScore, Scorecard,
    run_eval,
)

BASE_DIR = Path.cwd()


def _fingerprint() -> Fingerprint:
    return Fingerprint(
        total_pages=1, char_counts=[1], image_counts=[0],
        quality_sequence=["NORMAL_TEXT"], font_set_hash="a" * 64,
        family_pattern_hash="b" * 64,
    )


def _empty_case(case_id: str, pdf_sha256: str) -> Case:
    return Case(
        case_id=case_id, pdf_sha256=pdf_sha256,
        source=CaseSource.GOLDEN, status=CaseStatus.ACTIVE,
        labels=Labels(), fingerprint=_fingerprint(),
        provenance=Provenance(annotator="test", annotated_at="2026-07-24"),
    )


def _write_locators(path: Path, mapping: dict[str, str]) -> None:
    lines = ["[locators]"]
    for sha, p in mapping.items():
        # single-quote literal strings: Windows backslash paths need no escaping
        lines.append(f"'{sha}' = '{p}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── schema validation ──────────────────────────────────────────────────

def test_scorecard_schema_roundtrip():
    sc = Scorecard(
        per_case=[PerCaseScore(
            case_id="case-x", status="ran",
            frozen=FrozenScores(L1_f1=1.0, L2_hit=0.9, L3_acc=1.0, L4_acc=0.8),
        )],
        aggregates=Aggregates(
            micro=0.9, macro=0.925,
            by_unit=[ByUnit(unit="default", n=1, low_n=True)], skipped_count=0,
        ),
        merge_gate=MergeGate(any_active_below_floor=False,
                             macro_regressed=False, decision="allow"),
    )
    dumped = json.dumps(sc.model_dump())
    reloaded = Scorecard.model_validate_json(dumped)
    assert reloaded.model_dump() == sc.model_dump()


def test_percase_status_rejects_bad_value():
    with pytest.raises(ValueError):
        PerCaseScore(case_id="c", status="bogus")


# ── SKIPPED semantics: locator -> nonexistent path ─────────────────────

def test_missing_pdf_is_skipped_not_failure(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case = _empty_case("case-skip", "deadbeef" * 8)
    dump_case(case, cases_dir / "case-skip.json")

    loc = cases_dir / "locators.local.toml"
    _write_locators(loc, {case.pdf_sha256: str(tmp_path / "nope.pdf")})

    sc, exit_code = run_eval(
        base_dir=BASE_DIR, cases_dir=cases_dir, locators_path=loc,
        data_root=tmp_path / "data", baseline_path=tmp_path / "baseline.json",
    )
    assert exit_code == 0
    assert sc.aggregates.skipped_count == 1
    p = sc.per_case[0]
    assert p.status == "skipped" and p.skip_reason == "pdf_missing"


def test_no_locator_is_skipped(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case = _empty_case("case-noloc", "abc0" * 16)
    dump_case(case, cases_dir / "case-noloc.json")
    loc = cases_dir / "locators.local.toml"
    _write_locators(loc, {})

    sc, exit_code = run_eval(
        base_dir=BASE_DIR, cases_dir=cases_dir, locators_path=loc,
        data_root=tmp_path / "data", baseline_path=tmp_path / "baseline.json",
    )
    assert exit_code == 0
    assert sc.per_case[0].status == "skipped"
    assert sc.per_case[0].skip_reason == "no_locator"


# ── FAIL semantics: file present but sha mismatch ──────────────────────

def test_sha_mismatch_is_fail_nonzero_exit(tmp_path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case = _empty_case("case-fake", "0" * 64)  # sha that won't match any file
    dump_case(case, cases_dir / "case-fake.json")

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"not a real pdf")  # its sha != case.pdf_sha256
    loc = cases_dir / "locators.local.toml"
    _write_locators(loc, {case.pdf_sha256: str(fake_pdf)})

    sc, exit_code = run_eval(
        base_dir=BASE_DIR, cases_dir=cases_dir, locators_path=loc,
        data_root=tmp_path / "data", baseline_path=tmp_path / "baseline.json",
    )
    assert exit_code != 0
    assert sc.per_case[0].status == "fail"
    assert sc.per_case[0].skip_reason == "sha256_mismatch"


# ── synthetic small PDF: full run path, empty labels -> trivial scores ─

def test_synthetic_pdf_runs_and_scores(tmp_path):
    fitz = pytest.importorskip("fitz")
    from wbsgen.manifest import compute_file_hash

    pdf_path = tmp_path / "small.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello contract body text.")
    doc.save(str(pdf_path))
    doc.close()

    sha = compute_file_hash(pdf_path)
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case = _empty_case("case-syn", sha)
    dump_case(case, cases_dir / "case-syn.json")
    loc = cases_dir / "locators.local.toml"
    _write_locators(loc, {sha: str(pdf_path)})

    sc, exit_code = run_eval(
        base_dir=BASE_DIR, cases_dir=cases_dir, locators_path=loc,
        data_root=tmp_path / "data", baseline_path=tmp_path / "baseline.json",
    )
    assert exit_code == 0
    p = sc.per_case[0]
    assert p.status == "ran"
    # the full s01-s08 run produced a valid, in-range scorecard
    for v in (p.frozen.L1_f1, p.frozen.L2_hit, p.frozen.L3_acc, p.frozen.L4_acc):
        assert 0.0 <= v <= 1.0
    # recall-style layers with no expected labels score a trivial 1.0
    assert p.frozen.L2_hit == 1.0 and p.frozen.L3_acc == 1.0 and p.frozen.L4_acc == 1.0
