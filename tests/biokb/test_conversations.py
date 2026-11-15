"""会话式问答：会话存储、消息幂等与问答接口的会话关联。"""

from __future__ import annotations

import time

import pytest

from biokb.errors import KnowledgeError, NotFoundError
from biokb.web.app import create_app


def wait_job(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()["job"]
        if job["status"] in {"completed", "failed", "interrupted"}:
            return job
        time.sleep(0.05)
    raise AssertionError("任务未在超时内完成")


def test_conversation_repository_roundtrip(services):
    repository = services.repository
    conversation = repository.create_conversation()
    conversation_id = conversation["id"]
    assert repository.get_conversation(conversation_id)["title"] == "新对话"

    first = repository.append_message(conversation_id, "user", "第一个问题", message_id="t1_u")
    assert first["role"] == "user"
    # 相同 message_id 幂等跳过：任务重试不会重复写入。
    assert (
        repository.append_message(conversation_id, "user", "第一个问题", message_id="t1_u") is None
    )
    repository.append_message(
        conversation_id, "assistant", "第一个回答", run_id="qa_x", message_id="t1_a"
    )
    # 首个用户提问自动成为会话标题。
    assert repository.get_conversation(conversation_id)["title"] == "第一个问题"

    messages = repository.conversation_messages(conversation_id)
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[1]["run_id"] == "qa_x"
    assert repository.conversation_history(conversation_id) == [
        {"question": "第一个问题", "answer": "第一个回答"}
    ]

    repository.append_message(conversation_id, "user", "第二个问题", message_id="t2_u")
    # 未完成轮次（缺回答）不计入会话记忆。
    assert len(repository.conversation_history(conversation_id)) == 1

    listing = repository.list_conversations()
    assert [item["id"] for item in listing] == [conversation_id]
    assert listing[0]["message_count"] == 3

    repository.delete_conversation(conversation_id)
    with pytest.raises(NotFoundError):
        repository.get_conversation(conversation_id)


def test_conversation_unknown_ids(services):
    with pytest.raises(NotFoundError):
        services.repository.get_conversation("conv_missing")
    with pytest.raises(NotFoundError):
        services.repository.conversation_messages("conv_missing")
    with pytest.raises(NotFoundError):
        services.repository.delete_conversation("conv_missing")
    with pytest.raises(NotFoundError):
        services.repository.append_message("conv_missing", "user", "问题")


def test_conversation_api_crud(services):
    app = create_app(services.settings, services)
    client = app.test_client()

    created = client.post("/api/conversations", json={})
    assert created.status_code == 201
    conversation_id = created.get_json()["conversation"]["id"]

    listed = client.get("/api/conversations").get_json()["items"]
    assert [item["id"] for item in listed] == [conversation_id]

    detail = client.get(f"/api/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert detail.get_json()["messages"] == []

    assert client.get("/api/conversations/conv_missing").status_code == 404
    assert client.delete("/api/conversations/conv_missing").status_code == 404

    removed = client.delete(f"/api/conversations/{conversation_id}")
    assert removed.status_code == 200
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404


def test_ask_appends_turn_and_history(services):
    app = create_app(services.settings, services)
    client = app.test_client()
    conversation_id = client.post("/api/conversations", json={}).get_json()["conversation"]["id"]

    submitted = client.post(
        "/api/ask",
        json={"query": "线粒体如何参与细胞应激？", "conversation_id": conversation_id},
    )
    assert submitted.status_code == 202
    job = wait_job(client, submitted.get_json()["job"]["id"])
    assert job["status"] == "completed", job.get("error")
    result = job["result"]
    # 空库无证据走原文证据不足分支，不影响会话消息写入。
    assert result["mode"] == "insufficient_evidence"

    detail = client.get(f"/api/conversations/{conversation_id}").get_json()
    messages = detail["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "线粒体如何参与细胞应激？"
    assert messages[1]["content"] == result["answer"]
    assert messages[1]["run_id"] == result["id"]
    assert detail["conversation"]["title"].startswith("线粒体如何参与细胞应激")
    # 助手回答可在问答记录中按 run_id 复查。
    assert client.get(f"/api/history/{result['id']}").status_code == 200


def test_ask_rejects_unknown_conversation(services):
    app = create_app(services.settings, services)
    client = app.test_client()
    response = client.post(
        "/api/ask", json={"query": "任意问题", "conversation_id": "conv_missing"}
    )
    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"


def test_ask_history_marked_in_result(services):
    rag = services.rag
    history = [{"question": "上一轮问题", "answer": "上一轮回答"}]
    without = rag.ask("没有历史的提问")
    assert "conversation" not in without
    assert not without["prompt_version"].endswith("-conversation")
    with_history = rag.ask("带历史的提问", history=history)
    assert with_history["conversation"]["history_pairs"] == 1
    assert with_history["prompt_version"].endswith("-conversation")

    with pytest.raises(KnowledgeError):
        rag.ask("格式错误的历史", history=[{"question": "缺少回答字段"}])
