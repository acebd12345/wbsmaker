"""Stage 14: WBS validation — structure/semantic/source/coverage checks."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import ValidationIssue, ValidationReport, WbsNode


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    wbs_path = proj_dir / "13_merge" / "wbs.json"
    items_path = proj_dir / "10_extract" / "work_items.jsonl"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    out_dir = proj_dir / "14_validate"
    out_dir.mkdir(parents=True, exist_ok=True)

    wbs_nodes = [WbsNode(**n) for n in json.loads(wbs_path.read_text(encoding="utf-8"))]
    items = []
    for line in items_path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            items.append(json.loads(line))

    qualities = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        qualities[q["page_index"]] = q

    issues: list[ValidationIssue] = []
    issue_counter = 0

    # 1. Structure checks
    for node in wbs_nodes:
        if node.level > 0 and not node.code:
            issue_counter += 1
            issues.append(ValidationIssue(
                issue_id=f"issue-{issue_counter:04d}",
                category="structure",
                severity="warning",
                message=f"Node {node.node_id} has no WBS code",
                node_id=node.node_id,
            ))

        if node.level > 0 and not node.parent_id:
            issue_counter += 1
            issues.append(ValidationIssue(
                issue_id=f"issue-{issue_counter:04d}",
                category="structure",
                severity="warning",
                message=f"Node {node.node_id} has no parent",
                node_id=node.node_id,
            ))

    # 2. Source checks - EXPLICIT nodes should have source pages
    for node in wbs_nodes:
        if node.generation_type.value == "EXPLICIT" and not node.source_pages and node.level > 1:
            issue_counter += 1
            issues.append(ValidationIssue(
                issue_id=f"issue-{issue_counter:04d}",
                category="source",
                severity="info",
                message=f"EXPLICIT node {node.node_id} has no source pages",
                node_id=node.node_id,
            ))

    # 3. Coverage check — garbled/image sections
    garbled_count = sum(1 for q in qualities.values() if q["quality"] == "GARBLED_TEXT")
    image_count = sum(1 for q in qualities.values() if q["quality"] == "IMAGE_ONLY")
    if garbled_count > 0:
        issue_counter += 1
        issues.append(ValidationIssue(
            issue_id=f"issue-{issue_counter:04d}",
            category="coverage",
            severity="warning",
            message=f"{garbled_count} garbled pages not analyzed",
            needs_review=True,
        ))
    if image_count > 0:
        issue_counter += 1
        issues.append(ValidationIssue(
            issue_id=f"issue-{issue_counter:04d}",
            category="coverage",
            severity="info",
            message=f"{image_count} scanned pages not analyzed",
        ))

    # 4. Semantic check - nodes without work items
    leaf_nodes = [n for n in wbs_nodes if not n.children and n.level > 0]
    empty_leaves = [n for n in leaf_nodes if not n.work_items]
    if empty_leaves:
        issue_counter += 1
        issues.append(ValidationIssue(
            issue_id=f"issue-{issue_counter:04d}",
            category="semantic",
            severity="info",
            message=f"{len(empty_leaves)} leaf nodes have no work items",
        ))

    needs_review_count = sum(1 for i in issues if i.needs_review)
    report = ValidationReport(
        issues=issues,
        needs_review_count=needs_review_count,
        passed=not any(i.severity == "error" for i in issues),
    )

    (out_dir / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )

    return {"needs_review": needs_review_count > 0}
