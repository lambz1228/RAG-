"""构建全文补充题、真实问答快照和空白人工复核栏；不自动授予正确性分数。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biokb.config import Settings  # noqa: E402
from biokb.container import build_services  # noqa: E402
from biokb.errors import ProviderError  # noqa: E402
from biokb.evaluation import evaluate, write_report  # noqa: E402
from biokb.rag import RAGService  # noqa: E402


def review_fields() -> dict:
    return {
        "status": "pending",
        "reviewer": "",
        "reviewed_at": "",
        "correctness_0_2": None,
        "evidence_support_0_2": None,
        "coverage_0_2": None,
        "abstention_appropriate": None,
        "unsupported_claims": [],
        "notes": "",
    }


def run(data: Path, fulltexts: Path, development: Path, selection: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    specifications = [
        (
            "PMC12142011",
            "Evaluation of Prognostic Values",
            "Relationship of PEX Expression",
            "PEX 家族与乳腺癌的生物信息学研究使用哪个工具分析预后，关注哪两种生存结局？",
            "Kaplan-Meier plotter；总生存期 OS 和无复发生存期 RFS。",
        ),
        (
            "PMC12142011",
            "Relationship of PEX Expression",
            "mRNA Expression Analysis",
            "PEX 家族乳腺癌研究使用什么工具和统计检验分析表达量与临床病理特征之间的关系？",
            "bc-GenExMiner v4.8；Dunnett-Tukey-Kramer 和 Welch 检验。",
        ),
        (
            "PMC13199795",
            "Cell culture",
            "siRNA knockdown studies",
            "PEX13 和 PEX14 缺失影响脂肪酸氧化的研究使用了什么细胞系和基础培养基？",
            "HUH-7 细胞，RPMI 1640 培养基。",
        ),
        (
            "PMC13199795",
            "Induction of steatosis",
            "Oil Red O staining",
            "PEX13/PEX14 研究如何在细胞中诱导脂肪变性：游离脂肪酸组成比例、处理时长和工作浓度分别是多少？",
            "油酸和棕榈酸比例 2:1，处理 24 h，工作浓度 1 mM。",
        ),
        (
            "PMC13065327",
            "An alternative approach",
            "Results",
            "SynSAC 方法通过哪两个蛋白的诱导二聚化，使酵母停滞在减数分裂中期？",
            "去除动粒靶向结构域的 Mps1 和 Spc105/KNL1。",
        ),
        (
            "PMC13065327",
            "An alternative approach",
            "Results",
            "SynSAC 在减数第一次和第二次分裂中期的停滞效力有何差异，哪种磷酸酶限制第一次分裂的延迟？",
            "第二次分裂中期停滞更强；PP1 限制第一次分裂中期的 SAC 延迟。",
        ),
    ]
    items = []
    for index, (pmc, start, end, question, answer) in enumerate(specifications, 1):
        path = fulltexts / f"{pmc}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        text = document["content"]
        begin = text.index(start)
        finish = text.find(end, begin + len(start))
        quote = text[begin : finish if finish >= 0 else begin + 2500]
        items.append(
            {
                "qid": f"fulltext-{index:02}",
                "question": question,
                "gold_doc_ids": [document["source_id"]],
                "reference_answer_draft": answer,
                "evidence_quote": quote,
                "source_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "review": review_fields(),
            }
        )
    dataset = {
        "origin": "Codex 根据新增开放全文编写的 6 道补充题，未由专家标注",
        "split": "supplementary_fulltext",
        "human_review_status": "pending",
        "use": "冻结默认策略后的补充检查，不用于调参；样本小且题目明确点名研究，不能外推总体性能",
        "items": items,
    }
    write_report(dataset, output / "fulltext_questions.json")
    selected = json.loads(selection.read_text(encoding="utf-8"))["selected_strategy"]
    settings = replace(
        Settings.from_env(),
        data_dir=data.resolve(),
        default_retrieval=selected,
        request_timeout=90,
    )
    if settings.llm_backend == "disabled" or settings.embedding_backend == "hash":
        raise ValueError("请先配置并验证真实向量和生成模型")
    services = build_services(settings)
    try:
        write_report(
            evaluate(services.retrieval, dataset, [selected], 10),
            output / "fulltext_retrieval.json",
        )
        old = json.loads(development.read_text(encoding="utf-8"))["items"][:2]
        cases = [
            {"qid": row["qid"], "question": row["question"], "origin": "旧开发题"} for row in old
        ]
        cases += [
            {"qid": row["qid"], "question": row["question"], "origin": "新增全文题"}
            for row in items
        ]
        cases += [
            {
                "qid": "outside-domain",
                "question": "这份知识库能证明火星地铁一号线的营业时刻表吗？",
                "origin": "越界问题功能检查",
            }
        ]
        report = {
            "human_review_status": "pending",
            "configuration": settings.public(),
            "corpus": services.repository.corpus_manifest(),
            "selected_strategy": selected,
            "scope": "真实模型回答和证据快照，未进行专家评分；格式通过不等于事实正确",
            "items": [],
        }
        for case in cases:
            print(f"生成复核样本 {case['qid']}", flush=True)
            result = services.rag.ask(case["question"])
            report["items"].append({**case, "result": result, "review": review_fields()})
            write_report(report, output / "answers.json")
        protocols = [
            ("insufficient", "测试记录中的冷藏温度是多少？", ["接口测试记录：样本 A 编号为 21。"]),
            (
                "partial",
                "测试记录中的样本 A 编号和冷藏温度分别是多少？",
                ["接口测试记录：样本 A 编号为 21。"],
            ),
            (
                "conflict",
                "同一次试验的完成时间是多少？若两份记录冲突请明确指出。",
                [
                    "接口测试记录甲：试验 X 于第3天完成。",
                    "接口测试记录乙：同一试验 X 于第7天完成。",
                ],
            ),
        ]
        protocol_results = []
        for kind, question, texts in protocols:
            evidence = [{"title": "虚构接口测试材料，不是科研结论", "text": text} for text in texts]
            result = services.rag.model.answer(question, evidence)
            try:
                RAGService._validate_answer(result, len(evidence))
                validation = {"passed": True}
            except ProviderError as exc:
                validation = {"passed": False, "error": str(exc)}
            protocol_results.append(
                {
                    "kind": kind,
                    "question": question,
                    "evidence": evidence,
                    "response": result,
                    "format_validation": validation,
                    "review": review_fields(),
                }
            )
        write_report(
            {
                "scope": "虚构资料的状态协议检查，不是学术正确性评测",
                "generation_fingerprint": services.rag.model.fingerprint,
                "items": protocol_results,
            },
            output / "answer_protocol.json",
        )
    finally:
        services.jobs.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["data", "fulltexts", "development", "selection", "output"]:
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    run(args.data, args.fulltexts, args.development, args.selection, args.output)
