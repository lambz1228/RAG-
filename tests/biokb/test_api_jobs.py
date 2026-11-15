import io
import threading
import time
from dataclasses import replace

import pytest

from biokb.errors import ConflictError, KnowledgeError
from biokb.jobs import JobManager
from biokb.web.app import create_app


def wait_job(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get("/api/jobs/" + job_id).get_json()["job"]
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("任务未完成")


def test_upload_question_export_and_history_end_to_end(services):
    app = create_app(services.settings, services)
    client = app.test_client()
    assert client.get("/").status_code == 200
    content = b"EGFR activates RAS in a signaling pathway. This is a test corpus."
    response = client.post(
        "/api/documents/upload", data={"file": (io.BytesIO(content), "paper.txt")}
    )
    assert response.status_code == 202
    upload = wait_job(client, response.get_json()["job"]["id"])
    assert upload["status"] == "completed"
    document_id = upload["result"]["document"]["id"]
    assert client.get("/api/documents/" + document_id + "/download").data == content
    query = client.post(
        "/api/ask", json={"query": "EGFR activates RAS", "strategy": "bm25", "top_k": 1}
    )
    answer = wait_job(client, query.get_json()["job"]["id"])["result"]
    assert answer["mode"] == "extractive"
    assert answer["citations"][0]["document_id"] == document_id
    history = client.get("/api/history").get_json()["items"]
    assert history[0]["id"] == answer["id"]
    exported = client.get("/api/history/" + answer["id"] + "/export")
    assert exported.get_json()["citations"][0]["text"] == answer["citations"][0]["text"]
    assert "attachment" in exported.headers["Content-Disposition"]
    assert client.delete("/api/documents/" + document_id).status_code == 200
    assert client.get("/api/documents/" + document_id).status_code == 404
    assert client.get("/api/history/" + answer["id"]).status_code == 200


def test_config_never_exposes_keys_and_auth_is_enforced(services):
    settings = replace(
        services.settings,
        api_token="private-access",
        llm_api_key="private-model",
        embedding_api_key="private-vector",
    )
    app = create_app(settings, services)
    client = app.test_client()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/config").status_code == 401
    response = client.get("/api/config", headers={"Authorization": "Bearer private-access"})
    assert response.status_code == 200
    assert "private" not in response.get_data(as_text=True)


def test_cross_origin_and_rebinding_are_rejected(services):
    client = create_app(services.settings, services).test_client()
    assert (
        client.post(
            "/api/search", json={"query": "EGFR"}, headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/dashboard", headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"query": "x"},
        {"query": "EGFR", "top_k": True},
        {"query": "EGFR", "strategy": "invalid"},
    ],
)
def test_bad_search_payload_is_a_client_error(services, payload):
    client = create_app(services.settings, services).test_client()
    response = client.post("/api/search", json=payload)
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_upload_failure_is_visible_on_job(services):
    client = create_app(services.settings, services).test_client()
    result = client.post(
        "/api/documents/upload", data={"file": (io.BytesIO(b"not a pdf"), "broken.pdf")}
    ).get_json()
    job = wait_job(client, result["job"]["id"])
    assert job["status"] == "failed"
    assert job["error"]
    assert services.repository.stats()["documents"] == 0


def test_job_queue_capacity_and_failure_reporting():
    jobs = JobManager(workers=1, capacity=1)
    gate = threading.Event()
    first = jobs.submit("wait", lambda progress: gate.wait(3))
    try:
        with pytest.raises(ConflictError):
            jobs.submit("extra", lambda progress: None)
        gate.set()
        deadline = time.monotonic() + 3
        while jobs.get(first["id"])["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.01)

        def fail(progress):
            raise KnowledgeError("document cannot be read")

        second = jobs.submit("error", fail)
        while (
            jobs.get(second["id"])["status"] not in {"completed", "failed"}
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert jobs.get(second["id"])["error"] == "document cannot be read"
    finally:
        gate.set()
        jobs.close()


def test_frontend_has_separate_modules_and_safe_content_policy(services):
    client = create_app(services.settings, services).test_client()
    response = client.get("/")
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    for name in ["api.js", "ui.js", "documents.js", "research.js", "main.js", "app.css"]:
        assert client.get("/static/" + name).status_code == 200


def test_evaluation_job_saves_independent_result(services, corpus):
    client = create_app(services.settings, services).test_client()
    dataset = {
        "items": [
            {"qid": "one", "question": "mitochondrial energy ATP", "gold_doc_id": "mitochondria"}
        ]
    }
    response = client.post("/api/evaluations", json={"dataset": dataset, "k": 5})
    job = wait_job(client, response.get_json()["job"]["id"])
    assert job["status"] == "completed"
    result = job["result"]
    assert result["results"]["bm25"]["Hit@5"] == 1.0
    assert client.get("/api/evaluations").get_json()["items"][0]["id"] == result["id"]
    assert not client.get("/api/history").get_json()["items"]
