"""Manifest state machine: tracks stage status, input hashes, timestamps."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import StageRecord, StageStatus

STAGE_NAMES = [
    "parse", "quality", "layout", "subdoc", "toc", "section",
    "table", "assemble", "classify", "extract", "relate",
    "localwbs", "merge", "validate", "export",
]


class Manifest:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.path = project_dir / "manifest.json"
        self.pdf_sha256: str = ""
        self.project_id: str = project_dir.name
        self.stages: dict[str, StageRecord] = {}
        if self.path.exists():
            self._load()
        else:
            for name in STAGE_NAMES:
                self.stages[name] = StageRecord()

    def _load(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.pdf_sha256 = data.get("pdf_sha256", "")
        self.project_id = data.get("project_id", self.project_dir.name)
        for name in STAGE_NAMES:
            if name in data.get("stages", {}):
                self.stages[name] = StageRecord(**data["stages"][name])
            else:
                self.stages[name] = StageRecord()

    def save(self):
        data = {
            "project_id": self.project_id,
            "pdf_sha256": self.pdf_sha256,
            "stages": {k: v.model_dump() for k, v in self.stages.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def stage_status(self, name: str) -> StageStatus:
        return self.stages[name].status

    def mark_running(self, name: str, input_hash: str = ""):
        rec = self.stages[name]
        rec.status = StageStatus.RUNNING
        rec.input_hash = input_hash
        rec.started_at = _now()
        rec.error = ""
        self.save()

    def mark_done(self, name: str, output_files: list[str] | None = None):
        rec = self.stages[name]
        rec.status = StageStatus.DONE
        rec.finished_at = _now()
        if output_files:
            rec.output_files = output_files
        self.save()

    def mark_failed(self, name: str, error: str):
        rec = self.stages[name]
        rec.status = StageStatus.FAILED
        rec.finished_at = _now()
        rec.error = error
        self.save()

    def mark_stale(self, name: str):
        self.stages[name].status = StageStatus.STALE
        self.save()

    def should_skip(self, name: str, input_hash: str, force: bool = False) -> bool:
        if force:
            return False
        rec = self.stages[name]
        return rec.status == StageStatus.DONE and rec.input_hash == input_hash

    def first_pending(self) -> str | None:
        for name in STAGE_NAMES:
            if self.stages[name].status != StageStatus.DONE:
                return name
        return None

    def mark_downstream_stale(self, from_stage: str):
        idx = STAGE_NAMES.index(from_stage)
        for name in STAGE_NAMES[idx + 1:]:
            if self.stages[name].status == StageStatus.DONE:
                self.stages[name].status = StageStatus.STALE
        self.save()


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
