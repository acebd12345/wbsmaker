"""Evaluation runner v0 (LEARNING_V0 P3).

Scores the pipeline against frozen case labels and produces a layered
scorecard with SKIP / FAIL semantics and a merge-gate skeleton.

Per-case resolution (content-free, driven by cases/locators.local.toml):
  - no locator or the file is missing  -> SKIPPED   (not a failure)
  - file present but sha256 mismatches  -> FAIL      (guards same-name/diff-file)
  - file present and sha256 matches      -> run s01-s08 (mock-free) and score

Scoring is over the four frozen label layers:
  L1 subdocument split  -> F1 over (page_start, page_end, doc_type)
  L2 chapter anchors    -> hit-rate over (page, title_norm) in req-spec sections
  L3 exclusions         -> accuracy of expected doc_type ∈ profile excluded set
  L4 named tables       -> accuracy over (caption_norm, page_start, page_end)
"""
from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .cases import Case, L1Label, L2Label, L3Label, L4Label, load_case
from .config import load_config
from .manifest import Manifest, compute_file_hash
from .profile import load_profile

# floor threshold below which an active case blocks a merge
FLOOR = 0.6
# stages that are LLM-free and sufficient for L1-L4 scoring
_EVAL_STAGES = [
    "parse", "quality", "layout", "subdoc", "toc", "section", "table", "assemble",
]


# ── scorecard schema ───────────────────────────────────────────────────

class FrozenScores(BaseModel):
    L1_f1: float
    L2_hit: float
    L3_acc: float
    L4_acc: float

    def mean(self) -> float:
        return (self.L1_f1 + self.L2_hit + self.L3_acc + self.L4_acc) / 4.0


class PerCaseScore(BaseModel):
    case_id: str
    status: Literal["ran", "skipped", "fail"]
    skip_reason: str | None = None
    frozen: FrozenScores | None = None
    adjudicated: FrozenScores | None = None  # dual-column reserved for v1


class ByUnit(BaseModel):
    unit: str
    n: int
    low_n: bool


class Aggregates(BaseModel):
    micro: float
    macro: float
    by_unit: list[ByUnit] = Field(default_factory=list)
    skipped_count: int = 0


class MergeGate(BaseModel):
    any_active_below_floor: bool
    macro_regressed: bool
    decision: Literal["allow", "block"]


class Scorecard(BaseModel):
    per_case: list[PerCaseScore]
    aggregates: Aggregates
    merge_gate: MergeGate


# ── locators ───────────────────────────────────────────────────────────

def load_locators(path: Path) -> dict[str, str]:
    """Read {pdf_sha256 -> local path} from the gitignored locators file."""
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return dict(data.get("locators", {}))


# ── scoring primitives ─────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _f1(expected: set, produced: set) -> float:
    if not expected and not produced:
        return 1.0
    tp = len(expected & produced)
    prec = tp / len(produced) if produced else 0.0
    rec = tp / len(expected) if expected else 0.0
    if prec + rec == 0:
        return 0.0
    return 2 * prec * rec / (prec + rec)


def _hit_rate(expected: set, produced: set) -> float:
    if not expected:
        return 1.0
    return len(expected & produced) / len(expected)


def score_l1(expected: list[L1Label], subdocs: list[dict]) -> float:
    exp = {(l.page_start, l.page_end, l.doc_type) for l in expected}
    prod = {(s["page_start"], s["page_end"], s["doc_type"]) for s in subdocs}
    return _f1(exp, prod)


def score_l2(expected: list[L2Label], sections: list[dict], subdocs: list[dict]) -> float:
    req_ids = {s["subdoc_id"] for s in subdocs
               if s["doc_type"] == "REQUIREMENT_SPECIFICATION"}
    prod = {(s["start_page"], _norm(s["title"]))
            for s in sections
            if s.get("subdoc_id") in req_ids and s.get("level", 1) == 1}
    exp = {(l.page, l.title_norm) for l in expected}
    return _hit_rate(exp, prod)


def score_l3(expected: list[L3Label], excluded_doc_types: list[str]) -> float:
    if not expected:
        return 1.0
    excluded = set(excluded_doc_types)
    ok = sum(1 for l in expected if l.doc_type in excluded)
    return ok / len(expected)


def score_l4(expected: list[L4Label], tables: list[dict]) -> float:
    prod = {(_norm(t.get("caption", "")), t["page_start"], t["page_end"])
            for t in tables if re.search(r"表\s*\d+", t.get("caption", ""))}
    exp = {(l.caption_norm, l.page_start, l.page_end) for l in expected}
    return _hit_rate(exp, prod)


# ── pipeline execution (s01-s08, LLM-free) ─────────────────────────────

def _stage_runner() -> dict:
    from .stages.s01_parse import run as s01
    from .stages.s02_quality import run as s02
    from .stages.s03_layout import run as s03
    from .stages.s04_subdoc import run as s04
    from .stages.s05_toc import run as s05
    from .stages.s06_section import run as s06
    from .stages.s07_table import run as s07
    from .stages.s08_assemble import run as s08
    return {
        "parse": s01, "quality": s02, "layout": s03, "subdoc": s04,
        "toc": s05, "section": s06, "table": s07, "assemble": s08,
    }


def _stage_input_hash(stage: str, m: Manifest) -> str:
    import hashlib
    h = hashlib.sha256()
    h.update(stage.encode())
    h.update(m.pdf_sha256.encode())
    idx = _EVAL_STAGES.index(stage)
    if idx > 0:
        prev = _EVAL_STAGES[idx - 1]
        h.update(m.stages[prev].finished_at.encode())
    return h.hexdigest()


def run_pipeline(proj_dir: Path, pdf_path: Path, pdf_sha: str, cfg: dict) -> None:
    """Ingest the PDF into proj_dir and run s01-s08 with manifest caching."""
    orig = proj_dir / "original"
    orig.mkdir(parents=True, exist_ok=True)
    dest = orig / "contract.pdf"
    if not dest.exists() or compute_file_hash(dest) != pdf_sha:
        shutil.copy2(pdf_path, dest)

    m = Manifest(proj_dir)
    m.pdf_sha256 = pdf_sha
    m.project_id = proj_dir.name
    m.save()

    runner = _stage_runner()
    for sname in _EVAL_STAGES:
        input_hash = _stage_input_hash(sname, m)
        if m.should_skip(sname, input_hash, force=False):
            continue
        m.mark_running(sname, input_hash)
        try:
            runner[sname](proj_dir, cfg, m)
            m.mark_done(sname)
        except Exception as e:
            m.mark_failed(sname, str(e))
            raise


# ── per-case evaluation ────────────────────────────────────────────────

def evaluate_case(
    case: Case,
    locators: dict[str, str],
    data_root: Path,
    cfg: dict,
    base_dir: Path,
) -> PerCaseScore:
    locator = locators.get(case.pdf_sha256)
    if not locator:
        return PerCaseScore(case_id=case.case_id, status="skipped",
                            skip_reason="no_locator")
    pdf_path = Path(locator)
    if not pdf_path.is_absolute():
        pdf_path = base_dir / pdf_path
    if not pdf_path.exists():
        return PerCaseScore(case_id=case.case_id, status="skipped",
                            skip_reason="pdf_missing")

    actual_sha = compute_file_hash(pdf_path)
    if actual_sha != case.pdf_sha256:
        return PerCaseScore(case_id=case.case_id, status="fail",
                            skip_reason="sha256_mismatch")

    proj_dir = data_root / f"eval_{case.case_id}"
    run_pipeline(proj_dir, pdf_path, case.pdf_sha256, cfg)

    subdocs = _read_json(proj_dir / "04_subdoc" / "subdocs.json")
    sections = _read_json(proj_dir / "06_section" / "sections.json")
    tables = _read_json(proj_dir / "07_table" / "tables.json")
    profile = load_profile(cfg, base_dir=base_dir)

    frozen = FrozenScores(
        L1_f1=score_l1(case.labels.L1, subdocs),
        L2_hit=score_l2(case.labels.L2, sections, subdocs),
        L3_acc=score_l3(case.labels.L3, profile.classify.excluded_doc_types),
        L4_acc=score_l4(case.labels.L4, tables),
    )
    return PerCaseScore(case_id=case.case_id, status="ran", frozen=frozen)


# ── aggregation & merge gate ───────────────────────────────────────────

def _label_weights(case: Case) -> dict[str, int]:
    return {
        "L1_f1": max(len(case.labels.L1), 1),
        "L2_hit": max(len(case.labels.L2), 1),
        "L3_acc": max(len(case.labels.L3), 1),
        "L4_acc": max(len(case.labels.L4), 1),
    }


def aggregate(per_case: list[PerCaseScore], cases: dict[str, Case]) -> Aggregates:
    ran = [p for p in per_case if p.status == "ran" and p.frozen]
    macro = sum(p.frozen.mean() for p in ran) / len(ran) if ran else 0.0

    num = den = 0.0
    for p in ran:
        w = _label_weights(cases[p.case_id])
        for layer, weight in w.items():
            num += getattr(p.frozen, layer) * weight
            den += weight
    micro = num / den if den else 0.0

    n = len(ran)
    by_unit = [ByUnit(unit="default", n=n, low_n=n < 3)] if n else []
    skipped = sum(1 for p in per_case if p.status == "skipped")
    return Aggregates(micro=micro, macro=macro, by_unit=by_unit,
                      skipped_count=skipped)


def merge_gate(
    per_case: list[PerCaseScore],
    aggregates: Aggregates,
    cases: dict[str, Case],
    baseline: dict | None,
) -> MergeGate:
    below = False
    for p in per_case:
        if p.status == "ran" and p.frozen:
            case = cases[p.case_id]
            if case.status.value == "active" and p.frozen.mean() < FLOOR:
                below = True
                break
    regressed = False
    if baseline:
        base_macro = baseline.get("aggregates", {}).get("macro")
        if base_macro is not None and aggregates.macro < base_macro - 1e-9:
            regressed = True
    decision = "block" if (below or regressed) else "allow"
    return MergeGate(any_active_below_floor=below, macro_regressed=regressed,
                     decision=decision)


# ── top-level runner ───────────────────────────────────────────────────

def run_eval(
    *,
    base_dir: Path,
    cases_dir: Path | None = None,
    locators_path: Path | None = None,
    data_root: Path | None = None,
    baseline_path: Path | None = None,
    cfg: dict | None = None,
) -> tuple[Scorecard, int]:
    """Evaluate every case; return (scorecard, exit_code).

    exit_code is non-zero iff any case FAILs (sha mismatch). SKIPPED cases
    never fail the run.
    """
    base_dir = base_dir
    cases_dir = cases_dir or base_dir / "cases"
    locators_path = locators_path or cases_dir / "locators.local.toml"
    data_root = data_root or base_dir / "data" / "projects"
    baseline_path = baseline_path or cases_dir / "baseline_scorecard.json"
    cfg = cfg if cfg is not None else load_config(base_dir)

    locators = load_locators(locators_path)
    cases: dict[str, Case] = {}
    for cf in sorted(cases_dir.glob("case-*.json")):
        c = load_case(cf)
        cases[c.case_id] = c

    per_case = [
        evaluate_case(c, locators, data_root, cfg, base_dir)
        for c in cases.values()
    ]
    aggregates = aggregate(per_case, cases)

    baseline = None
    if baseline_path.exists():
        baseline = _read_json(baseline_path)
    gate = merge_gate(per_case, aggregates, cases, baseline)

    scorecard = Scorecard(per_case=per_case, aggregates=aggregates, merge_gate=gate)
    exit_code = 1 if any(p.status == "fail" for p in per_case) else 0
    return scorecard, exit_code


def write_baseline_if_absent(scorecard: Scorecard, baseline_path: Path) -> bool:
    """Write the scorecard as baseline on first run; return True if written."""
    if baseline_path.exists():
        return False
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(scorecard.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True
