"""GraphRAG 本地检索的“仅检索”版本：复用 GraphRAG 自己的上下文构建逻辑，返回排好序的文档列表。

必须用 .venv-graphrag 里的 Python 运行（graphrag 1.2.x 依赖 pandas<3、numpy<2）：
    .venv-graphrag/Scripts/python.exe scripts/rag_eval/graphrag_retrieve.py questions.json out.json

排序规则与 GraphRAG local search 一致：先用实体描述向量找到与问题最相关的实体，
再按“实体排名、与该实体关联的关系数”对实体所在的文本块排序；文本块再映射回文档并去重。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRAPHRAG_ROOT = ROOT / "sci-illust-system" / "web_app" / "data" / "text_kb" / "biology" / "graphrag"


def main() -> int:
    questions_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    # 不传时使用 settings.yaml 中 local_search.top_k_entities（默认 10），与真实问答一致
    top_entities = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    import pandas as pd
    from graphrag.api.query import _get_embedding_store
    from graphrag.index.config.embeddings import entity_description_embedding
    from graphrag.config.load_config import load_config
    from graphrag.config.resolve_path import resolve_paths
    from graphrag.query.factory import get_local_search_engine
    from graphrag.query.indexer_adapters import (
        read_indexer_entities,
        read_indexer_relationships,
        read_indexer_reports,
        read_indexer_text_units,
    )

    out = GRAPHRAG_ROOT / "output"
    config = load_config(GRAPHRAG_ROOT)
    resolve_paths(config)  # 与 graphrag query 命令一致：把 lancedb 等相对路径转成绝对路径
    top_entities = top_entities or config.local_search.top_k_entities
    nodes = pd.read_parquet(out / "create_final_nodes.parquet")
    entities = pd.read_parquet(out / "create_final_entities.parquet")
    reports = pd.read_parquet(out / "create_final_community_reports.parquet")
    text_units = pd.read_parquet(out / "create_final_text_units.parquet")
    relationships = pd.read_parquet(out / "create_final_relationships.parquet")
    documents = pd.read_parquet(out / "create_final_documents.parquet")

    # 文本块 -> 文档文件名（不含扩展名），与评估集里的 gold_doc_id 对齐
    doc_title = {row["id"]: os.path.splitext(row["title"])[0] for _, row in documents.iterrows()}
    unit_doc = {}
    for _, row in text_units.iterrows():
        doc_ids = list(row["document_ids"])
        unit_doc[row["id"]] = doc_title.get(doc_ids[0], "") if doc_ids else ""

    engine = get_local_search_engine(
        config=config,
        reports=read_indexer_reports(reports, nodes, 2),
        text_units=read_indexer_text_units(text_units),
        entities=read_indexer_entities(nodes, entities, 2),
        relationships=read_indexer_relationships(relationships),
        covariates={"claims": []},
        description_embedding_store=_get_embedding_store(
            config_args=config.embeddings.vector_store, embedding_name=entity_description_embedding
        ),
        response_type="Multiple Paragraphs",
        system_prompt="",
    )
    builder = engine.context_builder

    from graphrag.query.context_builder.entity_extraction import map_query_to_entities
    from graphrag.query.context_builder.source_context import count_relationships

    items = json.loads(questions_path.read_text(encoding="utf-8"))["items"]
    results = {}
    for item in items:
        if not item.get("keep", True):
            continue
        selected = map_query_to_entities(
            query=item["question"],
            text_embedding_vectorstore=builder.entity_text_embeddings,
            text_embedder=builder.text_embedder,
            all_entities_dict=builder.entities,
            embedding_vectorstore_key=builder.embedding_vectorstore_key,
            k=top_entities,
            oversample_scaler=2,
        )
        rel_values = list(builder.relationships.values())
        ranked_units, seen_units = [], set()
        for index, entity in enumerate(selected):
            entity_rels = [r for r in rel_values if r.source == entity.title or r.target == entity.title]
            for unit_id in entity.text_unit_ids or []:
                if unit_id in seen_units or unit_id not in builder.text_units:
                    continue
                seen_units.add(unit_id)
                n_rel = count_relationships(entity_rels, builder.text_units[unit_id])
                ranked_units.append((index, -n_rel, unit_id))
        ranked_units.sort()
        ranked_docs = []
        for _, _, unit_id in ranked_units:
            doc = unit_doc.get(unit_id, "")
            if doc and doc not in ranked_docs:
                ranked_docs.append(doc)
        results[item["qid"]] = ranked_docs[:20]
        print(f"{item['qid']} 命中实体 {len(selected)}，候选文档 {len(ranked_docs)}", flush=True)

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
