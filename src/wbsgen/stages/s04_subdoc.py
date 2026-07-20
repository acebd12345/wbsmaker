"""Stage 04: Subdocument boundary detection — structured multi-signal approach."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import PageQuality, Subdocument, SubdocType


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pages_dir = proj_dir / "01_parse" / "pages"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    families_path = proj_dir / "03_layout" / "families.json"
    out_dir = proj_dir / "04_subdoc"
    out_dir.mkdir(parents=True, exist_ok=True)

    spec_names = cfg.get("spec_anchors", {}).get("names", [])

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

    # 1a. Quality-based zones
    for pi in range(total_pages):
        q = qualities.get(pi, "")
        if q == PageQuality.IMAGE_ONLY.value:
            labels[pi] = "SCANNED_PAGES"
        elif q == PageQuality.GARBLED_TEXT.value:
            labels[pi] = "SERVICE_PROPOSAL"

    # 1b. Footer family zones
    bid_footer_id = None
    eval_header_id = None
    for fam_id, pattern in family_patterns.items():
        if "投標" in pattern or "須知" in pattern:
            bid_footer_id = fam_id
        if "{n}/{n}/{n}" in pattern:
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
    for pi in range(total_pages):
        if labels[pi]:
            continue
        if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
            continue
        text = "".join(b.get("text", "") for b in all_pages[pi].get("blocks", []))
        if any(ch in text for ch in "│├┤┬┴┼─"):
            labels[pi] = "LAW_OR_POLICY"

    # ── Phase 2: Strict title anchors (before family extension) ────

    # Find ATTACHMENT start: page where first title block starts with "附件"
    # Must NOT be a TOC entry (no trailing dots)
    attachment_start = None
    for pi in range(total_pages):
        if labels[pi]:
            continue
        if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
            continue
        blks = all_pages[pi].get("blocks", [])
        for b in blks[:4]:
            raw_text = b.get("text", "")
            text = re.sub(r"\s+", "", raw_text)
            font_size = b.get("font_size", 0)
            if not text:
                continue
            # Skip TOC entries (contain consecutive dots)
            if "...." in raw_text or "…" in raw_text:
                continue
            if (re.match(r"附件\d|附件[一二三四五六七八九十]", text)
                    and font_size >= 14):
                attachment_start = pi
                break
        if attachment_start is not None:
            break

    # Find SIGNATURE page: "立契約人" in first blocks
    signature_page = None
    for pi in range(total_pages):
        if labels[pi]:
            continue
        if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
            continue
        blks = all_pages[pi].get("blocks", [])
        for b in blks[:4]:
            text = re.sub(r"\s+", "", b.get("text", ""))
            if re.match(r"立契約人", text):
                signature_page = pi
                break
        if signature_page is not None:
            break

    # Find TENDER_ANNOUNCEMENT: "招標公告" in title position
    # Search BEFORE extending eval guidelines to prevent overlap
    tender_start = None
    for pi in range(total_pages):
        if labels[pi]:
            continue
        if qualities.get(pi) != PageQuality.NORMAL_TEXT.value:
            continue
        blks = all_pages[pi].get("blocks", [])
        for b in blks[:6]:
            text = re.sub(r"\s+", "", b.get("text", ""))
            if "招標公告" in text or "公開評選" in text or "公開徵求" in text:
                tender_start = pi
                break
        if tender_start is not None:
            break

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

    subdocs = _build_subdocs_from_labels(labels, total_pages)

    (out_dir / "subdocs.json").write_text(
        json.dumps(
            [s.model_dump() for s in subdocs], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return None


def _build_subdocs_from_labels(labels: list[str], total_pages: int) -> list[Subdocument]:
    type_map = {
        "CONTRACT_BODY": (SubdocType.CONTRACT_BODY, "契約本文"),
        "ATTACHMENT": (SubdocType.ATTACHMENT, "附件"),
        "SIGNATURE_PAGE": (SubdocType.SIGNATURE_PAGE, "簽署頁"),
        "REQUIREMENT_SPECIFICATION": (SubdocType.REQUIREMENT_SPECIFICATION, "需求規範書"),
        "BID_INSTRUCTIONS": (SubdocType.BID_INSTRUCTIONS, "投標須知"),
        "EVALUATION_GUIDELINES": (SubdocType.EVALUATION_GUIDELINES, "評選須知"),
        "TENDER_ANNOUNCEMENT": (SubdocType.TENDER_ANNOUNCEMENT, "招標公告"),
        "SCANNED_PAGES": (SubdocType.SCANNED_PAGES, "掃描頁"),
        "LAW_OR_POLICY": (SubdocType.LAW_OR_POLICY, "法規文件"),
        "SERVICE_PROPOSAL": (SubdocType.SERVICE_PROPOSAL, "服務建議書"),
    }

    subdocs = []
    current = labels[0]
    start = 0

    for pi in range(1, total_pages):
        if labels[pi] != current:
            dt, title = type_map.get(current, (SubdocType.UNKNOWN, current))
            subdocs.append(Subdocument(
                subdoc_id=f"subdoc-{len(subdocs)+1:03d}",
                title=title, doc_type=dt,
                page_start=start, page_end=pi - 1,
                page_count=pi - start,
            ))
            start = pi
            current = labels[pi]

    dt, title = type_map.get(current, (SubdocType.UNKNOWN, current))
    subdocs.append(Subdocument(
        subdoc_id=f"subdoc-{len(subdocs)+1:03d}",
        title=title, doc_type=dt,
        page_start=start, page_end=total_pages - 1,
        page_count=total_pages - start,
    ))
    return subdocs
