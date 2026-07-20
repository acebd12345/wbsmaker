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


# ── inspect (stub) ─────────────────────────────────────────────────────

@app.command()
def inspect(
    project: str = typer.Argument(...),
    view: str = typer.Argument(...),
    id: str = typer.Option("", "--id"),
):
    """Inspect pipeline artifacts."""
    console.print(f"[yellow]inspect {view}: not yet implemented[/yellow]")


# ── rerun (stub) ───────────────────────────────────────────────────────

@app.command()
def rerun(
    project: str = typer.Argument(...),
    stage: str = typer.Option(..., "--stage"),
    section: str = typer.Option("", "--section"),
    table: str = typer.Option("", "--table"),
):
    """Partial re-run of a stage."""
    console.print(f"[yellow]rerun: not yet implemented[/yellow]")


# ── export (stub) ──────────────────────────────────────────────────────

@app.command(name="export")
def export_cmd(
    project: str = typer.Argument(...),
    f: str = typer.Option("xlsx,json,mermaid,csv", "-f"),
):
    """Export WBS in various formats."""
    console.print(f"[yellow]export: not yet implemented[/yellow]")


# ── goldtest (stub) ────────────────────────────────────────────────────

@app.command()
def goldtest(
    pdf: str = typer.Option("tests/golden/contract_11108.pdf", "--pdf"),
):
    """Run golden test against expected.json."""
    console.print(f"[yellow]goldtest: not yet implemented[/yellow]")


# ── Entry point ────────────────────────────────────────────────────────

def app_entry():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    app()
