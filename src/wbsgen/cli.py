"""CLI entry point for wbs tool."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table as RichTable

from .config import WBS_TOML_CONTENT, load_config
from .manifest import STAGE_NAMES, Manifest, compute_file_hash

app = typer.Typer(add_completion=False)
console = Console()

# ── Resolve project directory ──────────────────────────────────────────

def _data_dir() -> Path:
    return Path.cwd() / "data" / "projects"


def _project_dir(project: str) -> Path:
    return _data_dir() / project


# ── init ───────────────────────────────────────────────────────────────

@app.command()
def init():
    """Create wbs.toml and data/ skeleton in cwd (idempotent)."""
    toml_path = Path.cwd() / "wbs.toml"
    if not toml_path.exists():
        toml_path.write_text(WBS_TOML_CONTENT, encoding="utf-8")
        console.print("Created wbs.toml")
    else:
        console.print("wbs.toml already exists")
    data = Path.cwd() / "data" / "projects"
    data.mkdir(parents=True, exist_ok=True)
    console.print("data/ ready")


# ── ingest ─────────────────────────────────────────────────────────────

@app.command()
def ingest(
    pdf: str = typer.Argument(..., help="Path to PDF file"),
    project: str = typer.Option("", help="Project ID (auto-generated if empty)"),
):
    """Copy PDF into project, compute SHA-256, create manifest."""
    pdf_path = Path(pdf).resolve()
    if not pdf_path.exists():
        console.print(f"[red]PDF not found: {pdf_path}[/red]")
        raise typer.Exit(3)

    if not project:
        from datetime import date
        project = f"{pdf_path.stem}_{date.today().isoformat()}"

    proj_dir = _project_dir(project)
    orig_dir = proj_dir / "original"
    orig_dir.mkdir(parents=True, exist_ok=True)
    dest = orig_dir / "contract.pdf"

    if not dest.exists():
        shutil.copy2(pdf_path, dest)

    sha = compute_file_hash(dest)
    m = Manifest(proj_dir)
    m.pdf_sha256 = sha
    m.project_id = project
    m.save()
    console.print(project)


# ── status ─────────────────────────────────────────────────────────────

@app.command()
def status(project: str = typer.Argument(..., help="Project ID")):
    """Show stage status table."""
    proj_dir = _project_dir(project)
    if not proj_dir.exists():
        console.print(f"[red]Project not found: {project}[/red]")
        raise typer.Exit(3)

    m = Manifest(proj_dir)
    tbl = RichTable(title=f"Project: {project}")
    tbl.add_column("#", width=3)
    tbl.add_column("Stage", width=12)
    tbl.add_column("Status", width=10)
    tbl.add_column("Finished", width=26)
    for i, name in enumerate(STAGE_NAMES, 1):
        rec = m.stages[name]
        color = {
            "PENDING": "dim", "RUNNING": "yellow",
            "DONE": "green", "FAILED": "red", "STALE": "cyan",
        }.get(rec.status.value, "white")
        tbl.add_row(str(i), name, f"[{color}]{rec.status.value}[/{color}]", rec.finished_at or "")
    console.print(tbl)


# ── run ────────────────────────────────────────────────────────────────

@app.command(name="run")
def run_stages(
    project: str = typer.Argument(..., help="Project ID"),
    from_stage: str = typer.Option("", "--from", help="Start from this stage"),
    to_stage: str = typer.Option("", "--to", help="Stop after this stage"),
    stage: str = typer.Option("", "--stage", help="Run single stage"),
    force: bool = typer.Option(False, "--force", help="Ignore cache"),
):
    """Run pipeline stages sequentially."""
    proj_dir = _project_dir(project)
    if not proj_dir.exists():
        console.print(f"[red]Project not found: {project}[/red]")
        raise typer.Exit(3)

    cfg = load_config()
    m = Manifest(proj_dir)

    if stage:
        stages_to_run = [stage]
    else:
        start_idx = STAGE_NAMES.index(from_stage) if from_stage else 0
        end_idx = STAGE_NAMES.index(to_stage) + 1 if to_stage else len(STAGE_NAMES)
        if not from_stage and not force:
            first = m.first_pending()
            if first:
                start_idx = STAGE_NAMES.index(first)
        stages_to_run = STAGE_NAMES[start_idx:end_idx]

    runner = _get_stage_runner()
    has_needs_review = False

    for sname in stages_to_run:
        if sname not in runner:
            console.print(f"[yellow]Stage {sname}: not yet implemented, skipping[/yellow]")
            continue

        input_hash = _compute_stage_input_hash(proj_dir, sname, m)
        if m.should_skip(sname, input_hash, force):
            console.print(f"[dim]Stage {sname}: skip (cached)[/dim]")
            continue

        console.print(f"[bold]Stage {sname}: running...[/bold]")
        m.mark_running(sname, input_hash)
        try:
            result = runner[sname](proj_dir, cfg, m)
            m.mark_done(sname)
            if result and result.get("needs_review"):
                has_needs_review = True
            console.print(f"[green]Stage {sname}: done[/green]")
        except Exception as e:
            m.mark_failed(sname, str(e))
            console.print(f"[red]Stage {sname}: FAILED - {e}[/red]")
            raise typer.Exit(3)

    if has_needs_review:
        raise typer.Exit(2)


def _get_stage_runner() -> dict:
    """Lazy-import stage runners."""
    runners = {}
    try:
        from .stages.s01_parse import run as s01
        runners["parse"] = s01
    except ImportError:
        pass
    try:
        from .stages.s02_quality import run as s02
        runners["quality"] = s02
    except ImportError:
        pass
    try:
        from .stages.s03_layout import run as s03
        runners["layout"] = s03
    except ImportError:
        pass
    try:
        from .stages.s04_subdoc import run as s04
        runners["subdoc"] = s04
    except ImportError:
        pass
    try:
        from .stages.s05_toc import run as s05
        runners["toc"] = s05
    except ImportError:
        pass
    try:
        from .stages.s06_section import run as s06
        runners["section"] = s06
    except ImportError:
        pass
    try:
        from .stages.s07_table import run as s07
        runners["table"] = s07
    except ImportError:
        pass
    try:
        from .stages.s08_assemble import run as s08
        runners["assemble"] = s08
    except ImportError:
        pass
    try:
        from .stages.s09_classify import run as s09
        runners["classify"] = s09
    except ImportError:
        pass
    try:
        from .stages.s10_extract import run as s10
        runners["extract"] = s10
    except ImportError:
        pass
    try:
        from .stages.s11_relate import run as s11
        runners["relate"] = s11
    except ImportError:
        pass
    try:
        from .stages.s12_localwbs import run as s12
        runners["localwbs"] = s12
    except ImportError:
        pass
    try:
        from .stages.s13_merge import run as s13
        runners["merge"] = s13
    except ImportError:
        pass
    try:
        from .stages.s14_validate import run as s14
        runners["validate"] = s14
    except ImportError:
        pass
    try:
        from .stages.s15_export import run as s15
        runners["export"] = s15
    except ImportError:
        pass
    return runners


def _compute_stage_input_hash(proj_dir: Path, stage: str, m: Manifest) -> str:
    """Compute a hash representing the inputs for a stage."""
    import hashlib
    h = hashlib.sha256()
    h.update(stage.encode())
    h.update(m.pdf_sha256.encode())
    # Include previous stage's completion time as dependency
    idx = STAGE_NAMES.index(stage)
    if idx > 0:
        prev = STAGE_NAMES[idx - 1]
        h.update(m.stages[prev].finished_at.encode())
    return h.hexdigest()


# ── auto ───────────────────────────────────────────────────────────────

@app.command()
def auto(pdf: str = typer.Argument(..., help="Path to PDF file")):
    """One-shot: ingest + run all stages + export."""
    pdf_path = Path(pdf).resolve()
    if not pdf_path.exists():
        console.print(f"[red]PDF not found: {pdf_path}[/red]")
        raise typer.Exit(3)

    from datetime import date
    project = f"{pdf_path.stem}_{date.today().isoformat()}"

    # ingest
    proj_dir = _project_dir(project)
    orig_dir = proj_dir / "original"
    orig_dir.mkdir(parents=True, exist_ok=True)
    dest = orig_dir / "contract.pdf"
    if not dest.exists():
        shutil.copy2(pdf_path, dest)
    sha = compute_file_hash(dest)
    m = Manifest(proj_dir)
    m.pdf_sha256 = sha
    m.project_id = project
    m.save()
    console.print(f"Project: {project}")

    # run all stages
    cfg = load_config()
    runner = _get_stage_runner()
    has_needs_review = False

    for sname in STAGE_NAMES:
        if sname not in runner:
            console.print(f"[yellow]Stage {sname}: not yet implemented, skipping[/yellow]")
            continue

        input_hash = _compute_stage_input_hash(proj_dir, sname, m)
        if m.should_skip(sname, input_hash, False):
            console.print(f"[dim]Stage {sname}: skip (cached)[/dim]")
            continue

        console.print(f"[bold]Stage {sname}: running...[/bold]")
        m.mark_running(sname, input_hash)
        try:
            result = runner[sname](proj_dir, cfg, m)
            m.mark_done(sname)
            if result and result.get("needs_review"):
                has_needs_review = True
            console.print(f"[green]Stage {sname}: done[/green]")
        except Exception as e:
            m.mark_failed(sname, str(e))
            console.print(f"[red]Stage {sname}: FAILED - {e}[/red]")
            raise typer.Exit(3)

    # Print summary
    _print_summary(proj_dir)

    if has_needs_review:
        raise typer.Exit(2)


def _print_summary(proj_dir: Path):
    """Print coverage summary after auto run."""
    summary_path = proj_dir / "02_quality" / "summary.json"
    if summary_path.exists():
        import json
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        console.print(f"\n[bold]Coverage Summary[/bold]")
        console.print(f"  Total pages: {s.get('total_pages', '?')}")
        console.print(f"  Normal text: {s.get('normal_text', '?')}")
        console.print(f"  Image only:  {s.get('image_only', '?')}")
        console.print(f"  Garbled:     {s.get('garbled_text', '?')}")

    exports_dir = proj_dir / "exports"
    if exports_dir.exists():
        for f in sorted(exports_dir.iterdir()):
            console.print(f"  Output: {f}")


# ── inspect ────────────────────────────────────────────────────────────

@app.command()
def inspect(
    project: str = typer.Argument(...),
    view: str = typer.Argument(...),
    id: str = typer.Option("", "--id"),
):
    """Inspect pipeline artifacts (pages|subdocs|sections|tables|items|wbs|issues|trace)."""
    import json
    proj_dir = _project_dir(project)
    if not proj_dir.exists():
        console.print(f"[red]Project not found: {project}[/red]")
        raise typer.Exit(3)

    if view == "pages":
        _inspect_pages(proj_dir)
    elif view == "subdocs":
        _inspect_subdocs(proj_dir)
    elif view == "sections":
        _inspect_sections(proj_dir)
    elif view == "tables":
        _inspect_tables(proj_dir)
    elif view == "items":
        _inspect_items(proj_dir)
    elif view == "wbs":
        _inspect_wbs(proj_dir)
    elif view == "issues":
        _inspect_issues(proj_dir)
    elif view == "trace":
        _inspect_trace(proj_dir, id)
    else:
        console.print(f"[red]Unknown view: {view}[/red]")


def _inspect_pages(proj_dir: Path):
    import json
    p = proj_dir / "02_quality" / "summary.json"
    if p.exists():
        s = json.loads(p.read_text(encoding="utf-8"))
        console.print(f"Total: {s['total_pages']}  Normal: {s['normal_text']}  Image: {s['image_only']}  Garbled: {s['garbled_text']}")


def _inspect_subdocs(proj_dir: Path):
    import json
    p = proj_dir / "04_subdoc" / "subdocs.json"
    if not p.exists():
        console.print("[yellow]Not available[/yellow]"); return
    subdocs = json.loads(p.read_text(encoding="utf-8"))
    tbl = RichTable(title="Subdocuments")
    tbl.add_column("ID"); tbl.add_column("Title"); tbl.add_column("Type"); tbl.add_column("Pages")
    for s in subdocs:
        tbl.add_row(s["subdoc_id"], s["title"], s["doc_type"], f"p{s['page_start']+1}-p{s['page_end']+1}")
    console.print(tbl)


def _inspect_sections(proj_dir: Path):
    import json
    p = proj_dir / "06_section" / "sections.json"
    if not p.exists():
        console.print("[yellow]Not available[/yellow]"); return
    sections = json.loads(p.read_text(encoding="utf-8"))
    tbl = RichTable(title="Sections")
    tbl.add_column("ID"); tbl.add_column("Subdoc"); tbl.add_column("Title"); tbl.add_column("Pages")
    for s in sections:
        tbl.add_row(s["section_id"], s["subdoc_id"], s["title"][:40], f"p{s['start_page']+1}-p{s['end_page']+1}")
    console.print(tbl)


def _inspect_tables(proj_dir: Path):
    import json
    p = proj_dir / "07_table" / "tables.json"
    if not p.exists():
        console.print("[yellow]Not available[/yellow]"); return
    tables = json.loads(p.read_text(encoding="utf-8"))
    tbl = RichTable(title="Tables")
    tbl.add_column("ID"); tbl.add_column("Caption"); tbl.add_column("Pages"); tbl.add_column("Rows"); tbl.add_column("Cross-page")
    for t in tables:
        if t.get("caption"):
            tbl.add_row(t["table_id"], t["caption"][:30], f"p{t['page_start']+1}-p{t['page_end']+1}",
                       str(len(t.get("rows", []))), str(t.get("cross_page_merged", False)))
    console.print(tbl)


def _inspect_items(proj_dir: Path):
    import json
    p = proj_dir / "10_extract" / "work_items.jsonl"
    if not p.exists():
        console.print("[yellow]Not available[/yellow]"); return
    items = [json.loads(l) for l in p.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
    console.print(f"Total work items: {len(items)}")
    for it in items[:20]:
        console.print(f"  {it['item_id']}: {it['description'][:60]}")


def _inspect_wbs(proj_dir: Path):
    import json
    p = proj_dir / "13_merge" / "wbs.json"
    if not p.exists():
        console.print("[yellow]Not available[/yellow]"); return
    nodes = json.loads(p.read_text(encoding="utf-8"))
    for n in nodes:
        if n.get("level", 0) == 0:
            continue
        indent = "  " * (n["level"] - 1)
        code = n.get("code", "")
        console.print(f"{indent}{code:10s} {n['title'][:50]}")


def _inspect_issues(proj_dir: Path):
    import json
    p = proj_dir / "14_validate" / "report.json"
    if not p.exists():
        console.print("[yellow]Not available[/yellow]"); return
    report = json.loads(p.read_text(encoding="utf-8"))
    console.print(f"Passed: {report['passed']}  NEEDS_REVIEW: {report['needs_review_count']}")
    for iss in report.get("issues", []):
        console.print(f"  [{iss['severity']}] {iss['message']}")


def _inspect_trace(proj_dir: Path, node_id: str):
    import json
    if not node_id:
        console.print("[red]--id required for trace[/red]"); return
    wbs_path = proj_dir / "13_merge" / "wbs.json"
    items_path = proj_dir / "10_extract" / "work_items.jsonl"
    if not wbs_path.exists():
        console.print("[yellow]WBS not available[/yellow]"); return
    nodes = json.loads(wbs_path.read_text(encoding="utf-8"))
    node = next((n for n in nodes if n["node_id"] == node_id or n.get("code") == node_id), None)
    if not node:
        console.print(f"[red]Node not found: {node_id}[/red]"); return
    console.print(f"Node: {node.get('code', '')} {node['title']}")
    console.print(f"  Level: {node['level']}  Type: {node.get('generation_type', '')}")
    console.print(f"  Source pages: {[p+1 for p in node.get('source_pages', [])]}")
    if items_path.exists():
        items = {json.loads(l)["item_id"]: json.loads(l) for l in items_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()}
        for wi_id in node.get("work_items", []):
            wi = items.get(wi_id, {})
            console.print(f"  Work item {wi_id}: {wi.get('description', '')[:60]}")


# ── rerun ──────────────────────────────────────────────────────────────

@app.command()
def rerun(
    project: str = typer.Argument(...),
    stage: str = typer.Option(..., "--stage"),
    section: str = typer.Option("", "--section"),
    table: str = typer.Option("", "--table"),
):
    """Partial re-run: rerun a stage and mark downstream as stale."""
    proj_dir = _project_dir(project)
    if not proj_dir.exists():
        console.print(f"[red]Project not found: {project}[/red]")
        raise typer.Exit(3)

    cfg = load_config()
    m = Manifest(proj_dir)
    runner = _get_stage_runner()

    if stage not in runner:
        console.print(f"[red]Unknown stage: {stage}[/red]")
        raise typer.Exit(3)

    console.print(f"[bold]Re-running stage {stage}...[/bold]")
    input_hash = _compute_stage_input_hash(proj_dir, stage, m)
    m.mark_running(stage, input_hash)
    try:
        runner[stage](proj_dir, cfg, m)
        m.mark_done(stage)
        m.mark_downstream_stale(stage)
        console.print(f"[green]Stage {stage}: done. Downstream marked stale.[/green]")
    except Exception as e:
        m.mark_failed(stage, str(e))
        console.print(f"[red]Stage {stage}: FAILED - {e}[/red]")
        raise typer.Exit(3)


# ── export ─────────────────────────────────────────────────────────────

@app.command(name="export")
def export_cmd(
    project: str = typer.Argument(...),
    f: str = typer.Option("xlsx,json,mermaid,csv", "-f"),
):
    """Export WBS in various formats."""
    proj_dir = _project_dir(project)
    if not proj_dir.exists():
        console.print(f"[red]Project not found: {project}[/red]")
        raise typer.Exit(3)

    cfg = load_config()
    m = Manifest(proj_dir)
    runner = _get_stage_runner()

    if "export" not in runner:
        console.print("[red]Export stage not available[/red]")
        raise typer.Exit(3)

    runner["export"](proj_dir, cfg, m)
    m.mark_done("export")
    console.print("[green]Export complete[/green]")

    exports_dir = proj_dir / "exports"
    if exports_dir.exists():
        for ef in sorted(exports_dir.iterdir()):
            console.print(f"  {ef}")


# ── goldtest ───────────────────────────────────────────────────────────

@app.command()
def goldtest(
    pdf: str = typer.Option("tests/golden/contract_11108.pdf", "--pdf"),
):
    """Run golden test: full pipeline on 11108 contract, then verify against expected.json."""
    import subprocess
    pdf_path = Path(pdf).resolve()
    if not pdf_path.exists():
        console.print(f"[red]PDF not found: {pdf_path}[/red]")
        raise typer.Exit(3)

    console.print("[bold]Running golden test...[/bold]")

    # Run pytest on golden tests
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/golden", "-x", "-q", "--tb=short"],
        capture_output=True, text=True, encoding="utf-8",
    )
    console.print(result.stdout)
    if result.stderr:
        console.print(result.stderr)

    if result.returncode != 0:
        console.print("[red]Golden test FAILED[/red]")
        raise typer.Exit(3)
    console.print("[green]Golden test PASSED[/green]")


# ── eval ───────────────────────────────────────────────────────────────

eval_app = typer.Typer(add_completion=False, help="Evaluation runner (scorecard).")
app.add_typer(eval_app, name="eval")


@eval_app.command("run")
def eval_run():
    """Score every case against frozen labels; write scorecard + merge gate."""
    from .eval import run_eval, write_baseline_if_absent

    base_dir = Path.cwd()
    scorecard, exit_code = run_eval(base_dir=base_dir)

    # scorecard -> reports/ (gitignored); baseline -> cases/ (committed, first run)
    reports_dir = base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    import json
    (reports_dir / "eval_scorecard.json").write_text(
        json.dumps(scorecard.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    baseline_path = base_dir / "cases" / "baseline_scorecard.json"
    wrote_baseline = write_baseline_if_absent(scorecard, baseline_path)

    tbl = RichTable(title="Eval Scorecard")
    tbl.add_column("Case"); tbl.add_column("Status")
    tbl.add_column("L1_f1"); tbl.add_column("L2_hit")
    tbl.add_column("L3_acc"); tbl.add_column("L4_acc")
    for p in scorecard.per_case:
        if p.frozen:
            f = p.frozen
            tbl.add_row(p.case_id, p.status, f"{f.L1_f1:.3f}",
                        f"{f.L2_hit:.3f}", f"{f.L3_acc:.3f}", f"{f.L4_acc:.3f}")
        else:
            tbl.add_row(p.case_id, f"{p.status} ({p.skip_reason})", "-", "-", "-", "-")
    console.print(tbl)

    a = scorecard.aggregates
    console.print(f"micro={a.micro:.3f}  macro={a.macro:.3f}  "
                  f"skipped={a.skipped_count}")
    g = scorecard.merge_gate
    console.print(f"merge_gate: decision={g.decision} "
                  f"(below_floor={g.any_active_below_floor}, "
                  f"regressed={g.macro_regressed})")
    if wrote_baseline:
        console.print(f"[green]baseline written: {baseline_path}[/green]")

    if exit_code != 0:
        console.print("[red]eval FAILED: one or more cases failed[/red]")
        raise typer.Exit(exit_code)


# ── Entry point ────────────────────────────────────────────────────────

def app_entry():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    app()
