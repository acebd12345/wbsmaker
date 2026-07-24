"""Static guard: business literals must not be hard-coded back into stages.

s04/s06/s09 must read all format-specific literals from the profile. This test
scans their source and fails if any deny-listed business term reappears. If a
term is genuinely unavoidable, add ``# profile-literal-ok: <reason>`` on that
line to allowlist it.
"""
from __future__ import annotations

import re
from pathlib import Path

STAGE_FILES = [
    Path("src/wbsgen/stages/s04_subdoc.py"),
    Path("src/wbsgen/stages/s06_section.py"),
    Path("src/wbsgen/stages/s09_classify.py"),
]

# Business terms that used to be hard-coded (LEARNING_V0 P1 deny-list).
DENY_TERMS = [
    "附件", "立契約人", "投標", "須知", "評選", "壹", "價金", "驗收",
    "招標", "公開評選", "掃描", "服務建議", "維運", "契約本文", "簽署",
]
# 第…條 article pattern (contract-body numbering) must also stay out of code.
DENY_REGEXES = [r"第.*條"]

ALLOW_MARKER = "profile-literal-ok"


def _violations(path: Path) -> list[str]:
    out = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for term in DENY_TERMS:
            if term in line:
                out.append(f"{path}:{lineno}: '{term}' -> {line.strip()}")
        for rx in DENY_REGEXES:
            if re.search(rx, line):
                out.append(f"{path}:{lineno}: /{rx}/ -> {line.strip()}")
    return out


def test_no_business_literals_in_stages():
    all_violations = []
    for f in STAGE_FILES:
        assert f.exists(), f"stage file missing: {f}"
        all_violations.extend(_violations(f))
    assert not all_violations, "business literals found in stage code:\n" + "\n".join(all_violations)
