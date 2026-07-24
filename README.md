# WBS Generator

Contract document parser and Work Breakdown Structure generator CLI.

Reads a PDF contract, extracts structure (subdocuments, sections, tables), classifies content, and generates a hierarchical WBS with traceability back to source pages.

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Usage

```powershell
# One-shot: PDF → Excel WBS
wbs auto path\to\contract.pdf

# Step-by-step
wbs init
wbs ingest path\to\contract.pdf
wbs run <project-id>
wbs export <project-id>

# Inspect results
wbs status <project-id>
wbs inspect <project-id> wbs
wbs inspect <project-id> issues

# Golden test (regression)
wbs goldtest

# Learning system (v0)
wbs eval run                    # score pipeline against cases/ (layered scorecard)
wbs review list <project-id>    # show NEEDS_REVIEW / low-confidence signals
wbs review annotate <project-id># emit prefilled annotation xlsx (L1 subdoc split)
wbs review accept <project-id>  # validate annotation -> save case (draft)
```

## Pipeline Stages

| # | Stage | Description |
|---|-------|-------------|
| 1 | parse | Extract text blocks and images from PDF |
| 2 | quality | Classify pages (normal / image-only / garbled) |
| 3 | layout | Detect running headers and footers |
| 4 | subdoc | Split into subdocuments |
| 5 | toc | Parse table of contents |
| 6 | section | Detect chapter boundaries |
| 7 | table | Extract and merge tables |
| 8 | assemble | Build section content with tables |
| 9 | classify | Classify content categories |
| 10 | extract | Extract work items |
| 11 | relate | Detect inter-item relationships |
| 12 | localwbs | Generate per-subdoc WBS trees |
| 13 | merge | Merge into global WBS |
| 14 | validate | Validate structure and coverage |
| 15 | export | Export to xlsx, json, csv, mermaid, interactive HTML |

## Configuration

Config merge order: built-in defaults ← `wbs.toml` ← `wbs.local.toml`.

- `wbs.toml` — shareable defaults (committed). Keep `mock = true` here.
- `wbs.local.toml` — machine-specific values (gitignored): real LLM endpoint, `mock = false`.

## Parsing rules live in profiles/

Subdocument split anchors, section numbering systems, and classification
keywords are declared in `profiles/default.toml`, not in code. To change
parsing behavior, edit the profile — canary tests
(`tests/unit/test_profile_p1.py`) verify the stages actually read it, and a
static check bans business literals in stage code. Before merging a profile
change, run `wbs eval run` and require `merge_gate: allow`.
