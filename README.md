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
| 15 | export | Export to xlsx, json, csv, mermaid |

## Configuration

Edit `wbs.toml` for LLM endpoint settings. Use `mock: true` for development without a live LLM.
