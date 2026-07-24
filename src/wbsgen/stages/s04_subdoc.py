"""Stage 04: Subdocument boundary detection — structured multi-signal approach.

All business literals (anchor patterns, footer/header hints, box-drawing chars,
quality→zone map, display titles) live in the profile (profiles/*.toml), loaded
via ``load_profile``. This module contains only the splitting *algorithm*.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import PageQuality, Subdocument, SubdocType
from ..profile import Profile, TitleAnchor, load_profile


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    profile = load_profile(cfg)
    sub = profile.subdoc

    pages_dir = proj_dir / "01_parse" / "pages"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    families_path = proj_dir / "03_layout" / "families.json"
    out_dir = proj_dir / "04_subdoc"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    qualities = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        qualities[q["page_index"]] = q["quality"]

    families = json.loads(families_path.read_text(encoding="utf-8"))

    page_files = sorted(pages_dir.glob("p*.json"))
    total_pages = len(page_files)
    all_pages = []
    for pf in page_files:
        all_pages.append(json.loads(pf.read_text(encoding="utf-8")))

    # Build footer family coverage
    footer_page_map: dict[int, str] = {}  # page_index -> family_id
    family_patterns: dict[str, str] = {}
    for fam in families:
        if fam["role"] != "RUNNING_FOOTER":
            continue
        pattern = fam.get("pattern", "")
        if re.fullmatch(r"\{n\}", pattern.strip()):
            continue
        family_patterns[fam["family_id"]] = pattern
        for bid in fam.get("block_ids", []):
            m = re.match(r"p(\d+)-", bid)
            if m:
                pi = int(m.group(1)) - 1
                footer_page_map[pi] = fam["family_id"]

    # Also check header families for the evaluation guidelines date pattern
    header_page_map: dict[int, str] = {}
    for fam in families:
        if fam["role"] != "RUNNING_HEADER":
            continue
        pattern = fam.get("pattern", "")
        if re.fullmatch(r"\{n\}", pattern.strip()):
            continue
        family_patterns[fam["family_id"]] = pattern
        for bid in fam.get("block_ids", []):
            m_bid = re.match(r"p(\d+)-", bid)
            if m_bid:
                pi = int(m_bid.group(1)) - 1
                header_page_map[pi] = fam["family_id"]

    # ── Phase 1: Identify definite zones ──────────────────────────

    labels = [""] * total_pages

    # 1a. Quality-based zones (profile.subdoc.quality_zone_map)
    for pi in range(total_pages):
        zone = sub.quality_zone_map.get(qualities.get(pi, ""))
        if zone:
            labels[pi] = zone

    # 1b. Footer / header family zones (profile hints)
    bid_footer_id = None
    eval_header_id = None
    bid_hints = sub.footer_family.bid_instructions_hints
    eval_token = sub.header_family.evaluation_date_pattern
    for fam_id, pattern in family_patterns.items():
        if any(h in pattern for h in bid_hints):
            bid_footer_id = fam_id
        if eval_token and eval_token in pattern:
            eval_header_id = fam_id

    # Label pages with bid instructions footer
    bid_pages = set()
    if bid_footer_id:
        for pi, fid in footer_page_map.items():
            if fid == bid_footer_id:
                labels[pi] = "BID_INSTRUCTIONS"
                bid_pages.add(pi)

    # Label pages with evaluation date header/footer
    eval_pages = set()
    if eval_header_id:
        for pi, fid in header_page_map.items():
            if fid == eval_header_id:
                labels[pi] = "EVALUATION_GUIDELINES"
                eval_pages.add(pi)

    # 1c. Box-drawing character pages → LAW_OR_POLICY
    box_chars = sub.box_drawing_chars
    for pi in range(total_pages):
        if labels[pi]:
            continue
        if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
            continue
        text = "".join(b.get("text", "") for b in all_pages[pi].get("blocks", []))
        if box_chars and any(ch in text for ch in box_chars):
            labels[pi] = "LAW_OR_POLICY"

    # ── Phase 2: Strict title anchors (before family extension) ────

    toc_markers = sub.toc_reject_markers

    # ATTACHMENT start: first non-TOC title block matching the attachment anchor
    attachment_start = _find_anchor_page(
        sub.anchor("attachment"), all_pages, qualities, labels, total_pages, toc_markers
    )

    # SIGNATURE page: block matching the signature anchor
    signature_page = _find_anchor_page(
        sub.anchor("signature"), all_pages, qualities, labels, total_pages, toc_markers
    )

    # TENDER_ANNOUNCEMENT: block matching the tender anchor
    # Search BEFORE extending eval guidelines to prevent overlap
    tender_start = _find_anchor_page(
        sub.anchor("tender"), all_pages, qualities, labels, total_pages, toc_markers
    )

    # Label tender announcement now (before eval extension)
    if tender_start is not None:
        for pi in range(tender_start, total_pages):
            if labels[pi]:
                break
            labels[pi] = "TENDER_ANNOUNCEMENT"

    # ── Phase 3: Extend family-based zones (after tender labeled) ──

    if eval_pages:
        first_eval = min(eval_pages)
        # Backward: fill gap between bid instructions and eval family
        if bid_pages:
            last_bid = max(bid_pages)
            for pi in range(last_bid + 1, first_eval):
                if not labels[pi]:
                    labels[pi] = "EVALUATION_GUIDELINES"

        # Forward: extend until hitting a labeled page
        last_eval = max(eval_pages)
        for pi in range(last_eval + 1, total_pages):
            if labels[pi]:
                break
            labels[pi] = "EVALUATION_GUIDELINES"

    # ── Phase 4: Build labels for detected anchors ────────────────

    # Requirement spec: pages between signature and bid instructions
    req_spec_start = None
    if signature_page is not None and bid_pages:
        req_spec_start = signature_page + 1
        first_bid = min(bid_pages)
        for pi in range(req_spec_start, first_bid):
            if not labels[pi]:
                labels[pi] = "REQUIREMENT_SPECIFICATION"

    # Attachment: pages from attachment_start to signature_page (exclusive)
    if attachment_start is not None:
        end = signature_page if signature_page is not None else (
            req_spec_start if req_spec_start is not None else (
                min(bid_pages) if bid_pages else total_pages
            )
        )
        for pi in range(attachment_start, end):
            if not labels[pi]:
                labels[pi] = "ATTACHMENT"

    # Signature page
    if signature_page is not None:
        labels[signature_page] = "SIGNATURE_PAGE"

    # Tender announcement
    if tender_start is not None:
        # Extend forward until we hit a labeled page
        for pi in range(tender_start, total_pages):
            if labels[pi]:
                break
            labels[pi] = "TENDER_ANNOUNCEMENT"

    # ── Phase 5: Default remaining to CONTRACT_BODY + forward fill ──

    if not labels[0]:
        labels[0] = "CONTRACT_BODY"

    # Forward fill
    last = labels[0]
    for pi in range(total_pages):
        if labels[pi]:
            last = labels[pi]
        else:
            labels[pi] = last

    # ── Phase 6: Build subdocuments from labels ───────────────────

    subdocs = _build_subdocs_from_labels(labels, total_pages, sub.doc_type_titles)

    (out_dir / "subdocs.json").write_text(
        json.dumps(
            [s.model_dump() for s in subdocs], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return None


def _find_anchor_page(
    anchor: TitleAnchor | None,
    all_pages: list[dict],
    qualities: dict[int, str],
    labels: list[str],
    total_pages: int,
    toc_markers: list[str],
) -> int | None:
    """Return the first unlabeled NORMAL_TEXT page whose leading blocks match
    the given title anchor. Match/font/TOC rules come entirely from the anchor.
    """
    if anchor is None:
        return None
    for pi in range(total_pages):
        if labels[pi]:
            continue
        if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
            continue
        blks = all_pages[pi].get("blocks", [])
        for b in blks[: anchor.scan_blocks]:
            raw_text = b.get("text", "")
            text = re.sub(r"\s+", "", raw_text)
            if not text:
                continue
            if anchor.toc_reject and any(m in raw_text for m in toc_markers):
                continue
            if b.get("font_size", 0) < anchor.min_font:
                continue
            if anchor.match_mode == "contains":
                matched = re.search(anchor.pattern, text) is not None
            else:
                matched = re.match(anchor.pattern, text) is not None
            if matched:
                return pi
    return None


def _build_subdocs_from_labels(
    labels: list[str], total_pages: int, doc_type_titles: dict[str, str]
) -> list[Subdocument]:
    def _resolve(label: str) -> tuple[SubdocType, str]:
        try:
            dt = SubdocType(label)
        except ValueError:
            dt = SubdocType.UNKNOWN
        title = doc_type_titles.get(label, label)
        return dt, title

    subdocs = []
    current = labels[0]
    start = 0

    for pi in range(1, total_pages):
        if labels[pi] != current:
            dt, title = _resolve(current)
            subdocs.append(Subdocument(
                subdoc_id=f"subdoc-{len(subdocs)+1:03d}",
                title=title, doc_type=dt,
                page_start=start, page_end=pi - 1,
                page_count=pi - start,
            ))
            start = pi
            current = labels[pi]

    dt, title = _resolve(current)
    subdocs.append(Subdocument(
        subdoc_id=f"subdoc-{len(subdocs)+1:03d}",
        title=title, doc_type=dt,
        page_start=start, page_end=total_pages - 1,
        page_count=total_pages - start,
    ))
    return subdocs
