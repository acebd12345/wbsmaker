"""Stage 01: PDF parsing — extract blocks, fonts, images per page."""
from __future__ import annotations

import json
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

from ..models import Block, PageParseResult


def run(proj_dir: Path, cfg: dict, manifest) -> dict | None:
    pdf_path = proj_dir / "original" / "contract.pdf"
    out_dir = proj_dir / "01_parse"
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    all_fonts: dict[str, dict] = {}
    blocks_file = out_dir / "blocks.jsonl"

    with open(blocks_file, "w", encoding="utf-8") as bf, \
         pdfplumber.open(str(pdf_path)) as plumber:

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            width = page.rect.width
            height = page.rect.height

            # Extract text blocks with font info
            blocks = []
            raw_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

            for blk_idx, blk in enumerate(raw_dict.get("blocks", [])):
                if blk["type"] != 0:  # text block only
                    continue

                block_text = ""
                font_name = ""
                font_size = 0.0
                is_bold = False

                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        block_text += span["text"]
                        if not font_name:
                            font_name = span.get("font", "")
                            font_size = span.get("size", 0.0)
                            flags = span.get("flags", 0)
                            is_bold = bool(flags & 2 ** 4)  # bit 4 = bold

                bbox = (blk["bbox"][0], blk["bbox"][1], blk["bbox"][2], blk["bbox"][3])
                block_id = f"p{page_idx+1:04d}-b{blk_idx:03d}"

                b = Block(
                    block_id=block_id,
                    page_index=page_idx,
                    bbox=bbox,
                    text=block_text,
                    font_name=font_name,
                    font_size=font_size,
                    is_bold=is_bold,
                )
                blocks.append(b)
                bf.write(b.model_dump_json() + "\n")

            # Image count
            image_count = len(page.get_images(full=True))

            # Collect fonts
            for f in page.get_fonts(full=True):
                fname = f[3]  # font name
                if fname not in all_fonts:
                    # f = (xref, ext, type, basefont, name, encoding)
                    all_fonts[fname] = {
                        "name": fname,
                        "encoding": f[5] if len(f) > 5 else "",
                    }

            # Check font ToUnicode via xref
            for f in page.get_fonts(full=True):
                xref = f[0]
                fname = f[3]
                if fname in all_fonts and "has_tounicode" not in all_fonts[fname]:
                    try:
                        font_obj = doc.xref_object(xref)
                        all_fonts[fname]["has_tounicode"] = "/ToUnicode" in font_obj
                    except Exception:
                        all_fonts[fname]["has_tounicode"] = False

            char_count = sum(len(b.text.replace(" ", "").replace("\n", "")) for b in blocks)

            pr = PageParseResult(
                page_index=page_idx,
                width=width,
                height=height,
                blocks=blocks,
                image_count=image_count,
                char_count=char_count,
                fonts_used=list({b.font_name for b in blocks if b.font_name}),
            )

            page_file = pages_dir / f"p{page_idx+1:04d}.json"
            page_file.write_text(pr.model_dump_json(indent=2), encoding="utf-8")

    # Write fonts.json
    fonts_file = out_dir / "fonts.json"
    fonts_file.write_text(
        json.dumps(all_fonts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    doc.close()
    return None
