"""Migrate the 11108 ground truth into cases/case-11108.json (P2).

Reads:
  - tests/golden/expected.json      (L1 subdoc boundaries, L3 excluded types)
  - data/projects/gold/*            (L2 req chapters, L4 named tables, fingerprint)
Writes:
  - cases/case-11108.json           (source=golden, status=active)

Requires the gold pipeline outputs to be present locally (data/ is gitignored).
Run from repo root:  py -3.12 scripts/migrate_case_11108.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wbsgen.cases import (  # noqa: E402
    Case, CaseSource, CaseStatus, L1Label, L2Label, L3Label, L4Label,
    Labels, Provenance, compute_fingerprint, dump_case,
)

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = json.loads((ROOT / "tests/golden/expected.json").read_text(encoding="utf-8"))
GOLD = ROOT / "data/projects/gold"
EXCLUDED_DOC_TYPES = [
    "BID_INSTRUCTIONS", "EVALUATION_GUIDELINES",
    "TENDER_ANNOUNCEMENT", "LAW_OR_POLICY",
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def build() -> Case:
    manifest = json.loads((GOLD / "manifest.json").read_text(encoding="utf-8"))
    pdf_sha = manifest["pdf_sha256"]

    # L1 — subdocument split from verified expected boundaries
    l1 = [
        L1Label(
            page_start=b["page_start"], page_end=b["page_end"],
            doc_type=b["type"], title=b["title"],
        )
        for b in EXPECTED["subdoc_boundaries"]
    ]

    # L2 — requirement-spec chapters located by (page, title_norm)
    subdocs = json.loads((GOLD / "04_subdoc/subdocs.json").read_text(encoding="utf-8"))
    req_ids = {s["subdoc_id"] for s in subdocs if s["doc_type"] == "REQUIREMENT_SPECIFICATION"}
    sections = json.loads((GOLD / "06_section/sections.json").read_text(encoding="utf-8"))
    l2 = [
        L2Label(
            page=s["start_page"],
            title_norm=_norm(s["title"]),
            doc_type="REQUIREMENT_SPECIFICATION",
        )
        for s in sections
        if s["subdoc_id"] in req_ids and s.get("level", 1) == 1
    ]

    # L3 — the four excluded document classes
    l3 = [L3Label(doc_type=dt, priority="EXCLUDED") for dt in EXCLUDED_DOC_TYPES]

    # L4 — named tables (表N) with page ranges
    tables = json.loads((GOLD / "07_table/tables.json").read_text(encoding="utf-8"))
    l4 = [
        L4Label(
            caption_norm=_norm(t["caption"]),
            page_start=t["page_start"], page_end=t["page_end"],
        )
        for t in tables
        if re.search(r"表\s*\d+", t.get("caption", ""))
    ]

    return Case(
        case_id="case-11108",
        pdf_sha256=pdf_sha,
        source=CaseSource.GOLDEN,
        status=CaseStatus.ACTIVE,
        answer_schema_version=1,
        labels=Labels(L1=l1, L2=l2, L3=l3, L4=l4),
        fingerprint=compute_fingerprint(GOLD),
        provenance=Provenance(
            annotator="manual-ground-truth",
            annotated_at="2026-07-24",
            reviewer=None,
            reviewer_same=None,
        ),
    )


def main() -> None:
    case = build()
    out = ROOT / "cases" / "case-11108.json"
    dump_case(case, out)
    print(f"wrote {out}")
    print(f"L1={len(case.labels.L1)} L2={len(case.labels.L2)} "
          f"L3={len(case.labels.L3)} L4={len(case.labels.L4)}")


if __name__ == "__main__":
    main()
