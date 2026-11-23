"""自动裁判拒绝非法评分，不能把解析失败当成有效分数。"""

import hashlib
import json
import runpy
import sqlite3
from pathlib import Path

import pytest

validate = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/evaluate_answers_auto.py")
)["validate_judgment"]


def judgment():
    return {
        "correctness": 2,
        "completeness": 1,
        "unsupported": False,
        "citation_support": "supported",
        "refusal": "not_applicable",
        "reason": "答案正确但遗漏一个成员。",
    }


def test_auto_judge_keeps_distinct_dimensions():
    value = validate(judgment())
    assert value["correctness"] == 2
    assert value["completeness"] == 1
    assert value["unsupported"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("correctness", True),
        ("completeness", 3),
        ("unsupported", "false"),
        ("citation_support", "yes"),
        ("refusal", None),
        ("reason", ""),
    ],
)
def test_auto_judge_rejects_invalid_fields(key, value):
    row = judgment()
    row[key] = value
    with pytest.raises(ValueError):
        validate(row)


@pytest.mark.parametrize("backend", ["ollama", "openai_compatible"])
def test_auto_judge_binds_inputs_blinds_arms_and_keeps_failures(tmp_path, monkeypatch, backend):
    from biokb.config import Settings

    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/evaluate_answers_auto.py")
    )
    evaluate = module["evaluate"]
    run = tmp_path / "run"
    (run / "snapshot").mkdir(parents=True)
    with sqlite3.connect(run / "snapshot/knowledge.sqlite3") as conn:
        conn.execute("CREATE TABLE documents(id TEXT, text TEXT)")
        conn.execute("INSERT INTO documents VALUES ('d1', 'ABC')")
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "qid": "q1",
                        "question": "成员？",
                        "answerable": True,
                        "reference_answer": "ABC",
                        "evidence": [
                            {
                                "document_id": "d1",
                                "start_offset": 0,
                                "end_offset": 3,
                                "sha256": hashlib.sha256(b"ABC").hexdigest(),
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run / "protocol.json").write_text(
        json.dumps(
            {
                "configuration": {"llm_model": "generator"},
                "planned_answers": 2,
                "question_ids": ["q1"],
                "arms": [{"name": "secret_arm"}, {"name": "failed_arm"}],
                "dataset": {"sha256": hashlib.sha256(dataset.read_bytes()).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    rows = [
        {"qid": "q1", "arm": "secret_arm", "answer": "ABC", "mode": "generated"},
        {"qid": "q1", "arm": "failed_arm", "error_type": "ProviderError"},
    ]
    (run / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(
        Settings,
        "from_env",
        classmethod(lambda cls: Settings(llm_backend=backend, llm_model="generator")),
    )
    captured = []

    def post(url, payload, key, timeout):
        captured.append((url, payload))
        message = {"content": json.dumps(judgment())}
        if backend == "ollama":
            return {"message": message}
        return {"choices": [{"message": message}]}

    monkeypatch.setattr("biokb.providers.post_json", post)
    with pytest.raises(ValueError, match="模型必须与被测模型不同"):
        evaluate(run, dataset, tmp_path / "same-model", " generator ")
    report = evaluate(run, dataset, tmp_path / "judged", "judge")
    ((url, payload),) = captured
    assert "secret_arm" not in json.dumps(payload)
    assert payload["model"] == "judge"
    if backend == "ollama":
        assert url.endswith("/api/chat")
        assert payload["think"] is False
        assert set(payload["format"]["required"]) == set(judgment())
    else:
        assert url.endswith("/chat/completions")
        assert payload["response_format"] == {"type": "json_object"}
    assert report["arms"]["secret_arm"]["scored"] == 1
    assert report["arms"]["failed_arm"]["failed"] == 1
    assert report["arms"]["failed_arm"]["fully_correct_rate"] is None
    assert len(list((tmp_path / "judged").glob("raw-*.json"))) == 1
    assert report["human_review_status"] is None
