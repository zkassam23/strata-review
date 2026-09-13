"""Background review jobs. In-memory registry plus a folder per job on disk so a restart can
still serve finished reports."""
from __future__ import annotations

import json
import logging
import shutil
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from strata_review import pipeline
from strata_review.settings import Settings

log = logging.getLogger(__name__)


class Job:
    def __init__(self, job_id: str, root: Path, unit: str | None, tenant: dict[str, Any]):
        self.id = job_id
        self.root = root
        self.unit = unit
        self.tenant = tenant
        self.stages = [{"key": k, "label": lbl, "status": "pending", "detail": ""} for k, lbl in pipeline.STAGES]
        self.state = "queued"        # queued | running | done | failed
        self.error: str | None = None
        self.created = datetime.now().isoformat(timespec="seconds")
        self.result_summary: dict[str, Any] = {}
        self.lock = threading.Lock()

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    def progress(self, key: str, status: str, detail: str) -> None:
        with self.lock:
            for s in self.stages:
                if s["key"] == key:
                    s["status"] = "done" if status == "done" else "run"
                    if detail:
                        s["detail"] = detail
            self.save()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "state": self.state, "error": self.error, "created": self.created, "unit": self.unit,
                "stages": self.stages, "summary": self.result_summary,
                "progress": round(sum(s["status"] == "done" for s in self.stages) / len(self.stages), 2)}

    def save(self) -> None:
        (self.root / "job.json").write_text(json.dumps(self.to_dict(), indent=1), "utf-8")


class JobStore:
    def __init__(self, settings: Settings, root: Path):
        self.settings = settings
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._load_finished()

    def _load_finished(self) -> None:
        for d in self.root.iterdir():
            meta = d / "job.json"
            if d.is_dir() and meta.exists():
                try:
                    data = json.loads(meta.read_text("utf-8"))
                except json.JSONDecodeError:
                    continue
                job = Job(data["id"], d, data.get("unit"), self.settings.tenant)
                job.stages, job.state, job.error, job.created = data["stages"], data["state"], data.get("error"), data.get("created", "")
                job.result_summary = data.get("summary", {})
                if job.state == "running":
                    job.state, job.error = "failed", "Server restarted while the job was running. Upload again."
                self.jobs[job.id] = job

    def create(self, unit: str | None, tenant: dict[str, Any]) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id, self.root / job_id, unit, tenant)
        job.input_dir.mkdir(parents=True, exist_ok=True)
        job.out_dir.mkdir(parents=True, exist_ok=True)
        self.jobs[job_id] = job
        job.save()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def start(self, job: Job) -> None:
        t = threading.Thread(target=self._run, args=(job,), name=f"job-{job.id}", daemon=True)
        t.start()

    def _run(self, job: Job) -> None:
        job.state = "running"
        job.save()
        try:
            settings = self.settings
            settings.extra["unit"] = job.unit
            settings.tenant = job.tenant
            state = pipeline.run(job.input_dir, settings=settings, progress=job.progress, out_dir=job.out_dir,
                                 workdir=job.root / "unzipped")
            r = state.result
            job.result_summary = {"overall_risk": r.overall_risk, "exposure_total": r.exposure_total,
                                  "red": sum(f.severity == "red" for f in r.flags), "amber": sum(f.severity == "amber" for f in r.flags),
                                  "pages": r.package.n_pages, "docs": r.package.n_docs, "cost": pipeline.cost_summary(state)}
            job.state = "done"
        except Exception as e:  # surfaced on the status page, never swallowed
            log.exception("job %s failed", job.id)
            job.state = "failed"
            job.error = f"{type(e).__name__}: {e}"
            (job.root / "traceback.txt").write_text(traceback.format_exc(), "utf-8")
        finally:
            job.save()

    def delete(self, job_id: str) -> None:
        job = self.jobs.pop(job_id, None)
        if job:
            shutil.rmtree(job.root, ignore_errors=True)
