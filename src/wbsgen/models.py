"""Pydantic data models for all pipeline stages."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────

class PageQuality(str, Enum):
    NORMAL_TEXT = "NORMAL_TEXT"
    IMAGE_ONLY = "IMAGE_ONLY"
    GARBLED_TEXT = "GARBLED_TEXT"
    MIXED = "MIXED"
    EMPTY = "EMPTY"


class BlockRole(str, Enum):
    BODY = "BODY"
    RUNNING_HEADER = "RUNNING_HEADER"
    RUNNING_FOOTER = "RUNNING_FOOTER"
    PAGE_NUMBER = "PAGE_NUMBER"
    TABLE_CELL = "TABLE_CELL"
    CAPTION = "CAPTION"
    TITLE = "TITLE"


class SubdocType(str, Enum):
    CONTRACT_BODY = "CONTRACT_BODY"
    ATTACHMENT = "ATTACHMENT"
    SIGNATURE_PAGE = "SIGNATURE_PAGE"
    REQUIREMENT_SPECIFICATION = "REQUIREMENT_SPECIFICATION"
    BID_INSTRUCTIONS = "BID_INSTRUCTIONS"
    EVALUATION_GUIDELINES = "EVALUATION_GUIDELINES"
    TENDER_ANNOUNCEMENT = "TENDER_ANNOUNCEMENT"
    SCANNED_PAGES = "SCANNED_PAGES"
    LAW_OR_POLICY = "LAW_OR_POLICY"
    SERVICE_PROPOSAL = "SERVICE_PROPOSAL"
    UNKNOWN = "UNKNOWN"


class ContentCategory(str, Enum):
    SCOPE = "SCOPE"
    DELIVERABLE = "DELIVERABLE"
    MILESTONE = "MILESTONE"
    CONSTRAINT = "CONSTRAINT"
    QUALITY = "QUALITY"
    MAINTENANCE = "MAINTENANCE"
    MANAGEMENT = "MANAGEMENT"
    UNCLASSIFIED = "UNCLASSIFIED"


class GenerationType(str, Enum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    MANUAL = "MANUAL"


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    STALE = "STALE"


# ── Block / Page ───────────────────────────────────────────────────────

class Block(BaseModel):
    block_id: str
    page_index: int
    bbox: tuple[float, float, float, float]
    text: str = ""
    font_name: str = ""
    font_size: float = 0.0
    is_bold: bool = False
    role: BlockRole = BlockRole.BODY
    exclude_from_content: bool = False
    exclude_from_header_detection: bool = False


class PageParseResult(BaseModel):
    page_index: int
    width: float
    height: float
    blocks: list[Block] = Field(default_factory=list)
    image_count: int = 0
    char_count: int = 0
    fonts_used: list[str] = Field(default_factory=list)


class PageQualityResult(BaseModel):
    page_index: int
    char_count: int = 0
    cjk_ratio: float = 0.0
    common_han_ratio: float = 0.0
    symbol_ratio: float = 0.0
    tounicode_ratio: float = 0.0
    quality: PageQuality = PageQuality.EMPTY


class QualitySummary(BaseModel):
    total_pages: int
    normal_text: int = 0
    image_only: int = 0
    garbled_text: int = 0
    mixed: int = 0
    empty: int = 0


# ── Layout Family ─────────────────────────────────────────────────────

class LayoutFamily(BaseModel):
    family_id: str
    pattern: str
    role: BlockRole
    page_ranges: list[tuple[int, int]] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)


# ── Subdocument ────────────────────────────────────────────────────────

class Subdocument(BaseModel):
    subdoc_id: str
    title: str = ""
    doc_type: SubdocType = SubdocType.UNKNOWN
    page_start: int
    page_end: int
    page_count: int = 0


# ── TOC Entry ──────────────────────────────────────────────────────────

class TocEntry(BaseModel):
    title: str
    printed_page: int | None = None
    pdf_page: int | None = None
    level: int = 1


# ── Section ────────────────────────────────────────────────────────────

class Section(BaseModel):
    section_id: str
    subdoc_id: str
    title: str
    level: int = 1
    start_page: int
    end_page: int
    start_block_id: str = ""
    end_block_id: str = ""


# ── Table ──────────────────────────────────────────────────────────────

class TableFragment(BaseModel):
    fragment_id: str
    page_index: int
    bbox: tuple[float, float, float, float]
    rows: list[list[str]] = Field(default_factory=list)
    header_row: list[str] = Field(default_factory=list)
    column_signature: str = ""


class Table(BaseModel):
    table_id: str
    caption: str = ""
    section_id: str = ""
    page_start: int
    page_end: int
    fragments: list[str] = Field(default_factory=list)
    header_row: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    cross_page_merged: bool = False
    needs_review: bool = False
    merge_confidence: float = 1.0


# ── Work Item / Relation / WBS ─────────────────────────────────────────

class WorkItem(BaseModel):
    item_id: str
    subdoc_id: str = ""
    section_id: str = ""
    table_id: str = ""
    description: str
    category: ContentCategory = ContentCategory.UNCLASSIFIED
    source_pages: list[int] = Field(default_factory=list)
    source_text: str = ""


class Relation(BaseModel):
    relation_id: str
    from_item: str
    to_item: str
    relation_type: str = ""


class WbsNode(BaseModel):
    node_id: str
    code: str = ""
    title: str
    level: int = 1
    parent_id: str = ""
    children: list[str] = Field(default_factory=list)
    work_items: list[str] = Field(default_factory=list)
    generation_type: GenerationType = GenerationType.EXPLICIT
    locked: bool = False
    source_pages: list[int] = Field(default_factory=list)


# ── Validation ─────────────────────────────────────────────────────────

class ValidationIssue(BaseModel):
    issue_id: str
    category: str  # structure / semantic / source / coverage
    severity: str  # error / warning / info
    message: str
    node_id: str = ""
    needs_review: bool = False


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)
    needs_review_count: int = 0
    passed: bool = True


# ── Manifest ───────────────────────────────────────────────────────────

class StageRecord(BaseModel):
    status: StageStatus = StageStatus.PENDING
    input_hash: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    output_files: list[str] = Field(default_factory=list)
