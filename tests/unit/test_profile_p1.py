"""P1 profile tests: loader, canary (profile drives behavior), anti-canary.

The canary/anti-canary pair is a *permanent* test (profile liveness), not a
one-shot migration check: it guarantees the stages keep reading the profile and
rules are never quietly written back into code.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from wbsgen.config import load_config
from wbsgen.manifest import Manifest
from wbsgen.profile import load_profile, resolve_profile_path
from wbsgen.stages import s04_subdoc, s06_section
from wbsgen.stages.s06_section import _match_chapter_title

GOLD = Path("data/projects/gold")
DEFAULT_TOML = Path("profiles/default.toml")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _write_mutated(tmp_path: Path, old: str, new: str) -> Path:
    text = DEFAULT_TOML.read_text(encoding="utf-8")
    assert old in text, f"literal to mutate not found in default.toml: {old!r}"
    mutated = tmp_path / "mutated.toml"
    mutated.write_text(text.replace(old, new), encoding="utf-8")
    return mutated


def _cfg_with_profile(path: Path | None = None) -> dict:
    cfg = load_config()
    if path is not None:
        cfg["profile"] = {"path": str(path)}
    return cfg


# ── loader ─────────────────────────────────────────────────────────────

def test_default_profile_loads():
    p = load_profile()
    assert p.profile_id == "default"
    assert p.version == 1


def test_missing_profile_raises(tmp_path):
    cfg = {"profile": {"path": str(tmp_path / "nope.toml")}}
    with pytest.raises(FileNotFoundError):
        load_profile(cfg)


# ── canary: known sample strings hit anchors/patterns ──────────────────

def test_canary_attachment_anchor():
    p = load_profile()
    anchor = p.subdoc.anchor("attachment")
    assert anchor is not None
    assert re.match(anchor.pattern, _norm("附件2：價金給付條件一覽表"))


def test_canary_contract_body_article():
    p = load_profile()
    blk = {"text": "第十二條 驗收及查驗", "font_size": 16}
    hit = _match_chapter_title(_norm("第十二條 驗收及查驗"), blk, "CONTRACT_BODY", p.section)
    assert hit is not None
    marker, title = hit
    assert marker == "第十二條"


def test_canary_requirement_spec_chapter():
    p = load_profile()
    blk = {"text": "壹、專案目標", "font_size": 16}
    hit = _match_chapter_title(_norm("壹、專案目標"), blk, "REQUIREMENT_SPECIFICATION", p.section)
    assert hit is not None
    assert hit[0] == "壹"


def test_canary_classify_rule_order():
    """Security keywords must be evaluated before function/system (order matters)."""
    p = load_profile()
    pats = [r.pattern for r in p.classify.title_rules]
    sec_i = next(i for i, x in enumerate(pats) if "資安" in x)
    fn_i = next(i for i, x in enumerate(pats) if "功能" in x)
    assert sec_i < fn_i


# ── anti-canary: mutate profile → stage output must change ─────────────

@pytest.fixture
def restore_gold_file():
    saved: dict[Path, bytes] = {}

    def _snapshot(path: Path):
        saved[path] = path.read_bytes()

    yield _snapshot

    for path, data in saved.items():
        path.write_bytes(data)


@pytest.mark.skipif(not GOLD.exists(), reason="gold project outputs not present")
def test_anticanary_subdoc_attachment(tmp_path, restore_gold_file):
    """Break the attachment anchor → s04 must produce zero ATTACHMENT subdocs."""
    out = GOLD / "04_subdoc" / "subdocs.json"
    restore_gold_file(out)

    mutated = _write_mutated(
        tmp_path,
        r"附件\d|附件[一二三四五六七八九十]",
        "ZZZ_IMPOSSIBLE_ANCHOR",
    )
    cfg = _cfg_with_profile(mutated)
    s04_subdoc.run(GOLD, cfg, Manifest(GOLD))

    subs = json.loads(out.read_text(encoding="utf-8"))
    n_attach = sum(1 for s in subs if s["doc_type"] == "ATTACHMENT")
    assert n_attach == 0, "mutated anchor still produced ATTACHMENT — stage ignored profile"


@pytest.mark.skipif(not GOLD.exists(), reason="gold project outputs not present")
def test_anticanary_section_requirement_spec(tmp_path, restore_gold_file):
    """Break the requirement-spec numbering → s06 must produce zero req sections."""
    out = GOLD / "06_section" / "sections.json"
    restore_gold_file(out)

    subs = json.loads((GOLD / "04_subdoc" / "subdocs.json").read_text(encoding="utf-8"))
    req_ids = {s["subdoc_id"] for s in subs if s["doc_type"] == "REQUIREMENT_SPECIFICATION"}

    mutated = _write_mutated(
        tmp_path,
        r"^({numeral})[、.．,](.+)",
        "^({numeral})ZZZ_IMPOSSIBLE(.+)",
    )
    cfg = _cfg_with_profile(mutated)
    s06_section.run(GOLD, cfg, Manifest(GOLD))

    secs = json.loads(out.read_text(encoding="utf-8"))
    n_req = sum(1 for s in secs if s["subdoc_id"] in req_ids)
    assert n_req == 0, "mutated numbering still produced req sections — stage ignored profile"
