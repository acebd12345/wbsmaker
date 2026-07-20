"""Stage 03: Layout family detection — running headers/footers."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import BlockRole, LayoutFamily, PageQuality
from ..textutil import normalize_for_family


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pages_dir = proj_dir / "01_parse" / "pages"
    quality_path = proj_dir / "02_quality" / "page_quality.jsonl"
    out_dir = proj_dir / "03_layout"
    out_dir.mkdir(parents=True, exist_ok=True)

    lcfg = cfg.get("layout", {})
    header_band = lcfg.get("header_band", 0.12)
    footer_band = lcfg.get("footer_band", 0.12)
    min_consecutive = lcfg.get("min_consecutive_pages", 2)

    # Load page qualities to skip garbled/image pages
    quality_map = {}
    for line in quality_path.read_text(encoding="utf-8").strip().split("\n"):
        q = json.loads(line)
        quality_map[q["page_index"]] = q["quality"]

    # Collect header/footer candidates from normal pages
    header_candidates: dict[int, list[dict]] = {}  # page_index -> list of blocks
    footer_candidates: dict[int, list[dict]] = {}

    page_files = sorted(pages_dir.glob("p*.json"))
    for pf in page_files:
        data = json.loads(pf.read_text(encoding="utf-8"))
        page_idx = data["page_index"]

        if quality_map.get(page_idx) != PageQuality.NORMAL_TEXT.value:
            continue

        height = data["height"]
        header_y = height * header_band
        footer_y = height * (1 - footer_band)

        for blk in data.get("blocks", []):
            bbox = blk["bbox"]
            y_top = bbox[1]
            text = blk["text"].strip()
            if not text:
                continue

            if y_top < header_y:
                header_candidates.setdefault(page_idx, []).append(blk)
            elif y_top > footer_y:
                footer_candidates.setdefault(page_idx, []).append(blk)

    # Build families: group by normalized pattern across consecutive pages
    families = []
    family_id_counter = 0

    # Process headers
    header_families = _find_families(
        header_candidates, "RUNNING_HEADER", min_consecutive
    )
    for fam in header_families:
        family_id_counter += 1
        fam.family_id = f"fam-{family_id_counter:03d}"
        families.append(fam)

    # Process footers
    footer_families = _find_families(
        footer_candidates, "RUNNING_FOOTER", min_consecutive
    )
    for fam in footer_families:
        family_id_counter += 1
        fam.family_id = f"fam-{family_id_counter:03d}"
        families.append(fam)

    # Mark blocks with family roles in page files
    family_block_ids = set()
    for fam in families:
        family_block_ids.update(fam.block_ids)

    for pf in page_files:
        data = json.loads(pf.read_text(encoding="utf-8"))
        modified = False
        for blk in data.get("blocks", []):
            if blk["block_id"] in family_block_ids:
                # Find which family this block belongs to
                for fam in families:
                    if blk["block_id"] in fam.block_ids:
                        blk["role"] = fam.role
                        blk["exclude_from_content"] = True
                        modified = True
                        break
        if modified:
            pf.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # Write families.json
    (out_dir / "families.json").write_text(
        json.dumps(
            [f.model_dump() for f in families], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    return None


def _find_families(
    candidates: dict[int, list[dict]],
    role: str,
    min_consecutive: int,
) -> list[LayoutFamily]:
    """Find repeating text patterns in header/footer blocks across consecutive pages."""
    # Map: normalized_pattern -> list of (page_index, block_id, y_position)
    pattern_occurrences: dict[str, list[tuple[int, str, float]]] = {}

    for page_idx in sorted(candidates.keys()):
        for blk in candidates[page_idx]:
            text = blk["text"].strip()
            if not text or len(text) < 2:
                continue
            norm = normalize_for_family(text)
            if not norm:
                continue
            pattern_occurrences.setdefault(norm, []).append(
                (page_idx, blk["block_id"], blk["bbox"][1])
            )

    families = []
    for pattern, occurrences in pattern_occurrences.items():
        if len(occurrences) < min_consecutive:
            continue

        # Find consecutive page runs
        pages = sorted(set(o[0] for o in occurrences))
        runs = _find_consecutive_runs(pages)

        # Only keep runs with >= min_consecutive pages
        valid_runs = [r for r in runs if len(r) >= min_consecutive]
        if not valid_runs:
            continue

        page_ranges = [(r[0], r[-1]) for r in valid_runs]
        valid_pages = set()
        for r in valid_runs:
            valid_pages.update(r)

        block_ids = [o[1] for o in occurrences if o[0] in valid_pages]

        fam = LayoutFamily(
            family_id="",  # assigned later
            pattern=pattern,
            role=BlockRole(role),
            page_ranges=page_ranges,
            block_ids=block_ids,
        )
        families.append(fam)

    return families


def _find_consecutive_runs(pages: list[int]) -> list[list[int]]:
    """Group page indices into consecutive runs."""
    if not pages:
        return []
    runs = []
    current = [pages[0]]
    for p in pages[1:]:
        if p == current[-1] + 1:
            current.append(p)
        else:
            runs.append(current)
            current = [p]
    runs.append(current)
    return runs
