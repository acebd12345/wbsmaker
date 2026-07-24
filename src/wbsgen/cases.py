"""Case library schema (LEARNING_V0 P2).

A case = one contract's *structural fingerprint* + *layered ground-truth answer*.

Design rules (from grok.md appendix 13 / LEARNING_V0):
  - ``source`` (where the answer came from) and ``status`` (governance state)
    are ORTHOGONAL columns — both are always present.
  - Labels must NEVER store runtime ids (subdoc-004, p0072-b003, …-sec-001):
    those are parse-parameter-dependent. L2 locates same-page chapters by
    (page, title_norm) and is resolved back to a block at eval time.
  - The fingerprint is pure structure (counts / quality sequence / hashes) and
    must contain NO contract text — the case file itself is safe to commit.
  - The PDF's physical path is NOT stored here; it lives in the gitignored
    ``cases/locators.local.toml`` keyed by pdf_sha256.
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


# ── enums: source × status are orthogonal ──────────────────────────────

class CaseSource(str, Enum):
    GOLDEN = "golden"        # human-verified in full
    CORRECTED = "corrected"  # produced by NEEDS_REVIEW correction
    AUTO = "auto"            # pipeline-produced, unreviewed (smoke only)


class CaseStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISPUTED = "disputed"
    RETIRED = "retired"


# ── layered labels ─────────────────────────────────────────────────────

class L1Label(BaseModel):
    """Subdocument split: page range + doc_type (+ optional display title)."""
    page_start: int
    page_end: int
    doc_type: str
    title: str | None = None


class L2Label(BaseModel):
    """Chapter anchor located by (page, normalized title) — never a block id."""
    page: int
    title_norm: str
    doc_type: str


class L3Label(BaseModel):
    """Exclusion / priority expectation, keyed by doc_type or title_norm."""
    priority: str
    doc_type: str | None = None
    title_norm: str | None = None


class L4Label(BaseModel):
    """Named table range located by normalized caption."""
    caption_norm: str
    page_start: int
    page_end: int


class Labels(BaseModel):
    L1: list[L1Label] = Field(default_factory=list)
    L2: list[L2Label] = Field(default_factory=list)
    L3: list[L3Label] = Field(default_factory=list)
    L4: list[L4Label] = Field(default_factory=list)


# runtime-id shapes that must never appear in labels
_RUNTIME_ID_RES = [
    re.compile(r"subdoc-\d"),
    re.compile(r"p\d+-b\d+"),
    re.compile(r"-sec-\d"),
    re.compile(r"\bsec-\d"),
    re.compile(r"fragment-\d"),
    re.compile(r"table-\d"),
]


# ── fingerprint (no contract text) ─────────────────────────────────────

class Fingerprint(BaseModel):
    total_pages: int
    char_counts: list[int]
    image_counts: list[int]
    quality_sequence: list[str]
    font_set_hash: str
    family_pattern_hash: str


class Provenance(BaseModel):
    annotator: str
    annotated_at: str
    reviewer: str | None = None
    reviewer_same: bool | None = None


# ── case ───────────────────────────────────────────────────────────────

class Case(BaseModel):
    case_id: str
    pdf_sha256: str
    source: CaseSource
    status: CaseStatus
    answer_schema_version: int = 1
    labels: Labels
    fingerprint: Fingerprint
    provenance: Provenance

    @model_validator(mode="after")
    def _no_runtime_ids_in_labels(self) -> "Case":
        blob = json.dumps(self.labels.model_dump(), ensure_ascii=False)
        for rx in _RUNTIME_ID_RES:
            m = rx.search(blob)
            if m:
                raise ValueError(
                    f"labels contain a forbidden runtime id shape: {m.group(0)!r}"
                )
        return self


# ── fingerprint computation from pipeline artifacts ────────────────────

def _sha(items: list[str]) -> str:
    h = hashlib.sha256()
    for it in items:
        h.update(it.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def compute_fingerprint(proj_dir: Path) -> Fingerprint:
    """Compute a content-free structural fingerprint from stage 01–03 outputs."""
    pages_dir = proj_dir / "01_parse" / "pages"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    families_path = proj_dir / "03_layout" / "families.json"

    page_files = sorted(pages_dir.glob("p*.json"))
    char_counts: list[int] = []
    image_counts: list[int] = []
    fonts: set[str] = set()
    for pf in page_files:
        d = json.loads(pf.read_text(encoding="utf-8"))
        char_counts.append(int(d.get("char_count", 0)))
        image_counts.append(int(d.get("image_count", 0)))
        fonts.update(d.get("fonts_used", []))

    quality_by_index: dict[int, str] = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        quality_by_index[q["page_index"]] = q["quality"]
    quality_sequence = [quality_by_index[i] for i in sorted(quality_by_index)]

    families = json.loads(families_path.read_text(encoding="utf-8"))
    patterns = sorted(f.get("pattern", "") for f in families)

    return Fingerprint(
        total_pages=len(page_files),
        char_counts=char_counts,
        image_counts=image_counts,
        quality_sequence=quality_sequence,
        font_set_hash=_sha(sorted(fonts)),
        family_pattern_hash=_sha(patterns),
    )


# ── I/O helpers ────────────────────────────────────────────────────────

def load_case(path: Path) -> Case:
    return Case.model_validate_json(path.read_text(encoding="utf-8"))


def dump_case(case: Case, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(case.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
