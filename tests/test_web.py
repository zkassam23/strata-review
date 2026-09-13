"""Web layer against the tiny package: upload -> status polling -> report page, link failure path."""
import os
import time
from urllib.parse import unquote

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["STRATA_LLM"] = "mock"
    os.environ["STRATA_JOB_DIR"] = str(tmp_path_factory.mktemp("jobs"))
    from fastapi.testclient import TestClient
    from web import app as web_app
    return TestClient(web_app.app)


def test_upload_page_uses_prototype_structure(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'class="drop"' in r.text and "Drop the package here" in r.text and "Kassam &amp; Associates" in r.text
    assert "Offline mode" in r.text


def test_link_that_cannot_be_read_fails_politely(client):
    r = client.post("/link-check", data={"link": "https://login.example.invalid/package"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "come across as files" in r.json()["message"]
    r = client.post("/jobs", data={"link": "https://login.example.invalid/package"}, follow_redirects=False)
    assert r.status_code == 303 and "come across as files" in unquote(r.headers["location"])


def test_upload_runs_job_and_serves_report(client, tiny_package):
    files = [("files", (f.name, f.read_bytes(), "application/pdf")) for f in sorted(tiny_package.glob("*.pdf"))]
    r = client.post("/jobs", files=files, data={"unit": "304"}, follow_redirects=False)
    assert r.status_code == 303
    job_url = r.headers["location"]
    job_id = job_url.rsplit("/", 1)[1]
    r = client.get(job_url)
    assert r.status_code in (200, 303)
    deadline = time.time() + 180
    while time.time() < deadline:
        j = client.get(f"/jobs/{job_id}/status.json").json()
        if j["state"] in ("done", "failed"):
            break
        time.sleep(1)
    assert j["state"] == "done", j.get("error")
    assert all(s["status"] == "done" for s in j["stages"])
    assert j["summary"]["pages"] == 3
    r = client.get(f"/jobs/{job_id}/report")
    assert r.status_code == 200
    assert 'data-view="client"' in r.text and 'id="view-agent"' in r.text and "Suite 304" in r.text
    assert client.get(f"/jobs/{job_id}/flags.json").status_code == 200
    assert client.get(f"/jobs/{job_id}/client.html").status_code == 200
    assert client.get(f"/jobs/{job_id}/nope.html").status_code == 404
