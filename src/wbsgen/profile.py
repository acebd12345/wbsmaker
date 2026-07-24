"""Profile loading — externalized format knowledge (v0).

A profile is a declarative TOML file describing the business literals that
drive subdocument splitting (s04), section detection (s06) and content
classification (s09). Stages read the profile instead of hard-coding rules.

Hard rule (LEARNING_V0 P1): if the requested profile file cannot be found,
``load_profile`` raises — it must NEVER silently fall back to hard-coded rules.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


# ── subdoc block ───────────────────────────────────────────────────────

class TitleAnchor(BaseModel):
    name: str
    doc_type: str
    pattern: str
    min_font: float = 0.0
    toc_reject: bool = False
    scan_blocks: int = 4
    match_mode: str = "prefix"  # prefix (re.match) | contains (re.search)


class FooterFamily(BaseModel):
    bid_instructions_hints: list[str] = Field(default_factory=list)


class HeaderFamily(BaseModel):
    evaluation_date_pattern: str = ""


class SubdocProfile(BaseModel):
    box_drawing_chars: str = ""
    toc_reject_markers: list[str] = Field(default_factory=list)
    quality_zone_map: dict[str, str] = Field(default_factory=dict)
    footer_family: FooterFamily = Field(default_factory=FooterFamily)
    header_family: HeaderFamily = Field(default_factory=HeaderFamily)
    title_anchors: list[TitleAnchor] = Field(default_factory=list)
    doc_type_titles: dict[str, str] = Field(default_factory=dict)

    def anchor(self, name: str) -> TitleAnchor | None:
        for a in self.title_anchors:
            if a.name == name:
                return a
        return None


# ── section block ──────────────────────────────────────────────────────

class SectionNumbering(BaseModel):
    numeral_set: list[str] = Field(default_factory=list)
    pattern: str
    min_font: float = 0.0
    toc_reject: bool = False
    strip_trailing_pagenum: bool = False
    title_format: str = "{marker} {title}"


class SectionProfile(BaseModel):
    doc_types: list[str] = Field(default_factory=list)
    toc_reject_markers: list[str] = Field(default_factory=list)
    numbering: dict[str, SectionNumbering] = Field(default_factory=dict)


# ── classify block ─────────────────────────────────────────────────────

class TitleRule(BaseModel):
    pattern: str
    category: str


class PriorityRule(BaseModel):
    priority: str
    wbs_relevance: float


class AttachmentPriority(BaseModel):
    primary_categories: list[str] = Field(default_factory=list)
    primary_relevance: float = 0.9
    secondary_relevance: float = 0.5


class ClassifyPriority(BaseModel):
    default: PriorityRule
    excluded: PriorityRule
    REQUIREMENT_SPECIFICATION: PriorityRule
    CONTRACT_BODY: PriorityRule
    ATTACHMENT: AttachmentPriority


class ClassifyProfile(BaseModel):
    excluded_doc_types: list[str] = Field(default_factory=list)
    skip_doc_types: list[str] = Field(default_factory=list)
    title_rules: list[TitleRule] = Field(default_factory=list)
    priority: ClassifyPriority


# ── reserved blocks (v0 fields only) ───────────────────────────────────

class ConservativeProfile(BaseModel):
    use_profile: str = "default"
    threshold_delta: float = 0.0
    forbid_long_forward_fill: bool = False
    always_emit_annotation: bool = False


# ── top-level ──────────────────────────────────────────────────────────

class Profile(BaseModel):
    profile_id: str
    version: int
    subdoc: SubdocProfile
    section: SectionProfile
    classify: ClassifyProfile
    quality: dict = Field(default_factory=dict)
    layout: dict = Field(default_factory=dict)
    conservative: ConservativeProfile = Field(default_factory=ConservativeProfile)


# ── loader ─────────────────────────────────────────────────────────────

def resolve_profile_path(cfg: dict | None = None, base_dir: Path | None = None) -> Path:
    """Resolve the profile TOML path from cfg / base_dir (no I/O check)."""
    if base_dir is None:
        base_dir = Path.cwd()
    prof_cfg = (cfg or {}).get("profile", {}) if cfg else {}
    explicit = prof_cfg.get("path")
    if explicit:
        return Path(explicit)
    name = prof_cfg.get("name", "default")
    return base_dir / "profiles" / f"{name}.toml"


def load_profile(cfg: dict | None = None, base_dir: Path | None = None) -> Profile:
    """Load and validate a profile.

    Resolution order:
      1. cfg["profile"]["path"] if present (used by tests / conservative mode)
      2. <base_dir>/profiles/<cfg.profile.name or "default">.toml

    Raises FileNotFoundError if the resolved file does not exist — silently
    falling back to hard-coded rules is forbidden.
    """
    path = resolve_profile_path(cfg, base_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"Profile not found: {path}. Profiles must exist; "
            f"the pipeline will not fall back to hard-coded rules."
        )
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Profile.model_validate(data)
