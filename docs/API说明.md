# HTTP API 说明

默认地址为 `http://127.0.0.1:5000`。JSON 请求使用 `Content-Type: application/json`。配置访问令牌时，除健康检查外的 API 使用 `Authorization: Bearer <访问令牌>`。跨来源请求被拒绝。

## 返回约定

业务成功通常返回 `success: true`；业务错误返回 `success: false`、`error`、`code`，并使用 400/404/409/502 等状态码。异步提交成功返回 HTTP 202 和 `job`；轮询任务时即使 HTTP 为 200，也应检查任务的 `status`，不能将提交成功当作处理完成。

| 方法与路径 | 说明 |
|---|---|
| `GET /api/health` | 服务版本与访问验证开关，不访问模型 |
| `GET /api/dashboard` | 文献、分块、向量、问答数量与公开配置 |
| `GET /api/config` | 模型名称与检索配置，只返回密钥是否已配置 |
| `GET /api/documents?q=&limit=50&offset=0` | 按标题/文件名筛选，返回分页与总数 |
| `GET /api/papers?q=&topic=&year=&fulltext=false&limit=20&offset=0` | 按论文聚合摘要与全文，返回分页、目录摘要和可选筛选值 |
| `POST /api/documents/upload` | multipart 文件上传，返回入库任务 |
| `GET /api/documents/<id>` | 文献原文、来源与全部分块 |
| `GET /api/documents/<id>/download` | 附件下载原始文件 |
| `DELETE /api/documents/<id>` | 删除知识库条目及索引；保留原始归档和历史快照 |
| `POST /api/index/rebuild` | JSON `{}`，提交向量重建任务 |
| `POST /api/search` | 同步返回原文证据 |
| `POST /api/ask` | 提交问答任务 |
| `GET /api/facets` | 主题、年份和资料类型的筛选值 |
| `GET /api/jobs` | 最近 30 条持久任务摘要 |
| `POST /api/jobs/<id>/retry` | JSON `{}`，重新提交失败或中断任务，生成新任务编号 |
| `GET /api/jobs/<id>` | 查询任务进度、结果或错误 |
| `GET /api/history` | 最近问答记录 |
| `GET /api/history/<id>` | 获取问答或评估快照 |
| `GET /api/history/<id>/export` | 下载快照 JSON |
| `GET /api/evaluations` | 最近检索评估记录 |
| `POST /api/evaluations` | 提交文档检索对比任务 |

## 文献上传

字段 `file` 必填；可选字段包括 `title`、`url`、`source`、`topic`、`year`、`content_type`、`doi`。支持 TXT、Markdown、PDF、DOCX、CSV、JSON，单文件不超过 25 MB。JSON 文献必须包含字符串 `content` 或 `text`，可提供来源元数据。扫描 PDF 未提取到文字时明确失败，不伪装成入库成功。

重复文件按原始字节 SHA-256 判定，返回 `duplicate: true`，不重复创建索引。不同编码或不同文件格式即使文字相同，也可能作为不同文件保存。

入库任务的 `result.document` 返回文献摘要与元数据，不重复携带整篇正文；需要正文时请求文献详情接口。

## 论文目录与研究范围

`GET /api/papers` 将资料元数据中一致的 PMID、PMCID、DOI 关联为论文条目，返回 `items`、`total`、`summary`、`topics` 和 `years`。`summary.papers` 是归并后的论文数，`summary.paper_records` 是可检索的摘要与全文资料数，`summary.reference_stubs` 是待补充条目数。单个 `item` 包含 `id`、正式编号、作者、期刊、年份、主题、`has_fulltext`、`preferred_document_id`、`document_ids` 和 `records`。仅同标题不自动合并；正式编号冲突时标注 `identity_conflict`。论文编号由现有元数据派生，资料增删或标识补全后可能变化，不应作为永久外部主键。

`q` 匹配标题、作者、期刊、关键词和正式编号；`fulltext` 仅接受 `true` 或 `false`；`limit` 为 1–100，`offset` 为 0–100000。`GET /api/documents/<id>` 另返回所属 `paper`，便于从资料回到论文目录。目录读取不加载全文或向量。

## 检索与问答请求

```json
{"query":"EGFR 如何参与信号转导？","strategy":"hybrid","top_k":5}
```

`query` 为 2–4000 个字符；`strategy` 可选 `bm25`、`dense`、`hybrid`、`hybrid_rerank`；`top_k` 为 1–50 的整数。检索额外接受 `document_id` 限制文献。检索和问答支持 `filters: {"topic":"主题","year":"2025","content_type":"fulltext"}`；文献列表支持对应查询参数。类型可为 abstract/fulltext/knowledge_note/reference_stub，年份为四位字符串。

检索和问答还可传 `paper_ids: ["paper_…"]`，最多 20 个有效论文编号。省略或空数组表示全库；未知或过期编号返回 404，无效格式返回 400，均不会静默放宽为全库。论文范围与 `filters` 取交集；检索的 `scope` 和问答的 `retrieval.scope` 记录当次论文标题、正式编号、关联资料编号及筛选条件。问答重试任务保留原请求的 `paper_ids`，论文后来消失时重试会失败并提示重新选择。

问答还可传 `context_neighbors`，取 0、1 或 2，默认 0；网页默认勾选时传 1。该字段在命中分块前后各选择相应数量的相邻分块，以保存原文偏移合并；若扩展后的完整片段超过上下文预算，则保留原命中片段。单篇论文范围可从同一资料选入至多 `top_k` 个证据，普通全库问答仍遵守每文献证据上限。网页“针对这篇论文提问”将 `top_k` 设为 10，直接调用 API 时仍需显式指定。

检索命中包含 `chunk_id`、`document_id`、`title`、`text`、`start_offset`、`end_offset`、`locator`、来源 `metadata` 以及排序得分。偏移对应保存后的规范化原文。

问答 `mode` 分别为：`extractive` 原文摘录；`generated` 模型生成；`partial` 部分支持；`provider_fallback` 模型失败后摘录；`insufficient_evidence` 证据不足。`citations` 包含证据编号和片段快照，`model` 在实际获得合法生成、部分回答或有召回证据的模型拒答时填写。`generation_fingerprint` 标识模型端点与提示词版本；`generation_validation` 保存格式尝试次数、错误和响应摘要。此指纹不保证服务商底层权重永不变化。

`context` 记录 `neighbors`、`source_count`、`max_chars`、`used_chars`、`retrieved_chunks` 和 `used_chunks`。扩展证据还携带 `retrieved_start_offset`、`retrieved_end_offset`、`retrieved_locator` 与 `context_chunk_ids`；证据正文和 `start_offset` / `end_offset` 指向实际展示的完整原文范围。证据超出正文字符预算时，只保留按排名能够容纳的完整片段，并在 `warnings` 中说明。`source_count` 与引用编号只描述证据覆盖，不证明事实正确性。

## 异步任务

任务状态为 `queued`、`running`、`completed`、`failed`、`interrupted`。`stage` 是当前处理阶段，`progress` 为 0–100。完成后读取 `result`；失败时读取 `error` 和 `code`。任务队列有界，满时返回 409。最近 100 条任务状态保存在 SQLite，重启后未完成任务标记 interrupted。失败/中断的问答、评估、重建索引可重新提交；上传需重新选择文件。原任务保留，重试产生新任务；这不是断点续传。已完成文献、问答及实验持久保存。

## 评估请求

```json
{
  "name":"开发集检索对比",
  "k":10,
  "strategies":["bm25","dense","hybrid"],
  "dataset":{"origin":"人工标注","items":[{"qid":"dev_001","question":"待评估问题","gold_doc_ids":["文献 source_id"]}]}
}
```

网页接口最多 200 题。评估结果记录数据集 SHA-256、语料内容摘要、向量及重排序模型指纹、索引修订号、逐题排名、失败类别和聚合指标。标准出处以文献 `metadata.source_id` 对齐，不使用标题相似度替代精确匹配。

`dataset_sha256` 针对解析后按键排序的 JSON 序列化结果计算，不是上传文件原始字节的摘要。结果另含 `k`、`candidate_chunks`、`created_at` 与不含密钥的运行配置。评估期间索引变化时，任务返回 `failed`，错误码为冲突类型。

标准出处不在知识库时任务失败。旧编号需通过人工或确定性原文核验后，在 `dataset.source_map` 提供 `{旧编号:库内source_id}` 映射；不得将已有库内编号改指其他文献。结果保存出处覆盖率、映射摘要，以及每题的原编号和对齐后编号。
