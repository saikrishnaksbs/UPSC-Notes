from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import upsc_notes.pipeline as pipeline_mod
import upsc_notes.web.app as app_mod
from upsc_notes.web import create_app

from .test_pipeline import SOURCE


@pytest.fixture()
def client(cfg, monkeypatch, fake_llm):
    monkeypatch.setattr(pipeline_mod, "build_chat_model", lambda c, model=None: fake_llm)
    monkeypatch.setattr(app_mod, "build_chat_model", lambda c, model=None: fake_llm)
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def _wait(client, job_id, statuses=("review", "failed"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["job"]["status"] in statuses and not job["busy"] and not any(p["status"] in ("pending", "generating") for p in job["proposals"]):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job did not finish: {job['job']}")


def test_upload_review_edit_apply(client, repo):
    assert client.get("/").status_code == 200
    st = client.get("/api/status").json()
    assert st["repo"]["is_repo"] and st["llm"]["model"]
    tax = client.get("/api/taxonomy").json()
    assert {s["name"] for s in tax["subjects"]} == {"Polity", "Science_and_technology", "Ancient_history"}

    r = client.post("/api/jobs", data={"tag": "PIB", "source_url": "https://pib.gov.in"}, files=[("files", ("ca.md", SOURCE.encode(), "text/markdown"))])
    assert r.status_code == 200, r.text
    job_id = r.json()[0]["id"]
    job = _wait(client, job_id)
    assert job["job"]["status"] == "review"
    create = next(p for p in job["proposals"] if p["action"] == "create")

    prev = client.get(f"/api/jobs/{job_id}/proposals/{create['id']}/preview").json()
    assert prev["note_path"].startswith("Polity/25-constitutional-bodies/") and "<h2>Why in News</h2>" in prev["html"]
    assert any(c["kind"] == "readme" for c in prev["changes"])

    # edit, move to another folder (no rewrite needed for a new note), approve, apply
    r = client.patch(f"/api/jobs/{job_id}/proposals/{create['id']}", json={"content": "## Why in News\n- Edited by a human.\n"})
    assert r.json()["edited"] is True
    r = client.patch(f"/api/jobs/{job_id}/proposals/{create['id']}", json={"action": "create", "subject": "Polity", "subfolder": "07-fundamental-rights"})
    assert r.json()["subfolder"] == "07-fundamental-rights" and r.json()["content"]
    assert client.patch(f"/api/jobs/{job_id}/proposals/{create['id']}", json={"status": "approved"}).json()["status"] == "approved"
    res = client.post(f"/api/jobs/{job_id}/apply", json={"commit": False}).json()
    assert res["applied"] == [create["id"]]
    note_path = next(w for w in res["written"] if w.startswith("Polity/07-fundamental-rights/03-"))
    assert "Edited by a human." in (repo / note_path).read_text()


def test_change_to_update_triggers_rewrite_and_skip_include(client):
    r = client.post("/api/jobs", data={"tag": "PIB"}, files=[("files", ("ca.md", SOURCE.encode(), "text/markdown"))])
    job_id = r.json()[0]["id"]
    job = _wait(client, job_id)
    create = next(p for p in job["proposals"] if p["action"] == "create")
    r = client.patch(
        f"/api/jobs/{job_id}/proposals/{create['id']}",
        json={"action": "update", "target_path": "Polity/25-constitutional-bodies/01-election-commission-of-india.md"},
    )
    assert r.status_code == 200 and r.json()["status"] == "pending"
    job = _wait(client, job_id)
    moved = next(p for p in job["proposals"] if p["id"] == create["id"])
    assert moved["action"] == "update" and moved["content"]

    skipped = next(t for t in job["topics"] if t["relevant"] is False)
    assert client.post(f"/api/jobs/{job_id}/topics/{skipped['id']}/include").status_code == 200
    job = _wait(client, job_id)
    assert any(skipped["id"] in p["topic_ids"] for p in job["proposals"])


def test_rejects_bad_uploads(client):
    r = client.post("/api/jobs", data={"tag": "x"}, files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400
    r = client.post("/api/jobs", data={"tag": "  "}, files=[("files", ("a.md", b"# a", "text/markdown"))])
    assert r.status_code == 400
