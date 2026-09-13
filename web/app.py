"""FastAPI web layer: upload, status, report. Three pages on the prototype's styling.

Run locally:  strata-review-web        (or: uvicorn web.app:app --reload)
No authentication yet. Single tenant from config/tenant.yaml; see web/tenants.py.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from strata_review import report as report_mod
from strata_review.settings import ROOT, load_settings

from .jobs import JobStore
from .tenants import get_tenant

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
settings = load_settings()
JOB_ROOT = Path(os.environ.get("STRATA_JOB_DIR", ROOT / "web_jobs"))
store = JobStore(settings, JOB_ROOT)
tenant = get_tenant(settings)

app = FastAPI(title="Strata review", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.loader = ChoiceLoader([FileSystemLoader(str(HERE / "templates")), FileSystemLoader(str(report_mod.TEMPLATES))])
templates.env.globals["cites_or_refuse"] = report_mod.cites_or_refuse

LINK_POLITE = ("That link needs a login we cannot pass, or did not point at a PDF or zip, so the documents have to "
               "come across as files. Download them and drop them above.")
SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()&-]+")


def _ctx(request: Request, **kw):
    return {"tenant": tenant, "mock": settings.llm_mode == "mock", **kw}


@app.get("/", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(request, "upload.html", _ctx(request, msg=request.query_params.get("msg", "")))


def _fetch_link(url: str, dest: Path) -> tuple[bool, str]:
    """Try the link server side first. Only a directly downloadable PDF or zip is accepted."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, LINK_POLITE
        with httpx.Client(follow_redirects=True, timeout=15) as client:
            r = client.get(url)
        ctype = r.headers.get("content-type", "").lower()
        name = Path(parsed.path).name or "download"
        if r.status_code != 200:
            return False, LINK_POLITE
        if "pdf" in ctype or name.lower().endswith(".pdf"):
            name = name if name.lower().endswith(".pdf") else name + ".pdf"
        elif "zip" in ctype or name.lower().endswith(".zip"):
            name = name if name.lower().endswith(".zip") else name + ".zip"
        else:
            return False, LINK_POLITE
        (dest / SAFE_NAME.sub("_", name)).write_bytes(r.content)
        return True, ""
    except Exception as e:  # network errors, timeouts, bad URLs all end in the same polite message
        log.info("link fetch failed for %s: %s", url, e)
        return False, LINK_POLITE


@app.post("/jobs")
async def create_job(request: Request, files: list[UploadFile] = File(default=[]), link: str = Form(default=""), unit: str = Form(default="")):
    files = [f for f in files if f.filename]
    if not files and not link.strip():
        return RedirectResponse("/?msg=" + "Add at least one PDF or zip first.", status_code=303)
    job = store.create(unit.strip() or None, tenant)
    for f in files:
        name = SAFE_NAME.sub("_", Path(f.filename).name)
        if not name.lower().endswith((".pdf", ".zip")):
            continue
        (job.input_dir / name).write_bytes(await f.read())
    if link.strip():
        ok, msg = _fetch_link(link.strip(), job.input_dir)
        if not ok and not any(job.input_dir.iterdir()):
            store.delete(job.id)
            return RedirectResponse("/?msg=" + msg, status_code=303)
    if not any(job.input_dir.iterdir()):
        store.delete(job.id)
        return RedirectResponse("/?msg=Only PDF and zip files can be read.", status_code=303)
    store.start(job)
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.post("/link-check")
def link_check(link: str = Form(default="")):
    """Used by the upload page's 'Add link' button: attempt server side, answer politely."""
    tmp = JOB_ROOT / "_linkcheck"
    tmp.mkdir(parents=True, exist_ok=True)
    ok, msg = _fetch_link(link.strip(), tmp) if link.strip() else (False, "Paste a link first.")
    for f in tmp.glob("*"):
        f.unlink()
    return JSONResponse({"ok": ok, "message": "Link reachable; it will be fetched when you start the review." if ok else msg})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def status_page(request: Request, job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404)
    if job.state == "done":
        return RedirectResponse(f"/jobs/{job_id}/report", status_code=303)
    return templates.TemplateResponse(request, "status.html", _ctx(request, job=job.to_dict()))


@app.get("/jobs/{job_id}/status.json")
def status_json(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404)
    return JSONResponse(job.to_dict())


@app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
def report_page(request: Request, job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404)
    if job.state != "done":
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    agent = (job.out_dir / "agent.html").read_text("utf-8")
    client = (job.out_dir / "client.html").read_text("utf-8")
    # embed only the report body of each standalone file (same template, same CSS)
    def body(html: str) -> str:
        s, e = html.index('<div class="rpt">'), html.rindex("</div>\n</div>\n</body>")
        return html[s:e]
    return templates.TemplateResponse(request, "report.html", _ctx(request, job=job.to_dict(), agent_body=body(agent), client_body=body(client)))


@app.get("/jobs/{job_id}/{name}")
def download(job_id: str, name: str):
    job = store.get(job_id)
    if not job or name not in ("agent.html", "client.html", "flags.json"):
        raise HTTPException(404)
    path = job.out_dir / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), filename=name)


def main() -> None:
    import uvicorn
    uvicorn.run("web.app:app", host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), reload=False)


if __name__ == "__main__":
    main()
