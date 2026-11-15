# API 说明

默认地址 `http://127.0.0.1:5000`。JSON 接口成功返回 `success:true`；失败包含 `error` 与 `code`。配置访问令牌后，通过 `Authorization: Bearer <token>` 访问，健康检查除外。

| 方法与路径 | 功能 |
| --- | --- |
| GET `/api/health` | 健康状态 |
| GET `/api/dashboard`、`/api/config` | 实际统计与不含密钥的公开配置 |
| GET `/api/literature/topics` | 六个主题与检索模板 |
| POST `/api/literature/search` | 官方文献发现 |
| POST `/api/literature/import` | 所选 PMID 来源复核并异步入库 |
| GET `/api/documents`、`/api/facets` | 文献分页与筛选 |
| POST `/api/documents/upload` | multipart 本地文件异步入库 |
| GET `/api/documents/<id>`、`/download` | 原文、分块和原始文件 |
| DELETE `/api/documents/<id>` | 移出文献与索引，保留归档和历史证据 |
| GET `/api/papers` | 论文目录与摘要、全文关联 |
| POST `/api/search` | 同步检索与原文候选 |
| POST `/api/ask` | 异步问答 |
| POST `/api/index/rebuild` | 在当前运行库重建索引 |
| GET `/api/jobs`、`/api/jobs/<id>` | 状态与任务结果 |
| GET `/api/history`、`/api/history/<id>`、`/export` | 历史问答与快照导出 |
| GET / POST `/api/conversations` | 会话列表与创建 |
| POST `/api/evaluations` | 文档检索评估 |
| POST `/api/answer-evaluations` | 上传协调者运行包与真实评分CSV，汇总回答质量并保存 |

文献发现请求：

```json
{"topic":"mitochondria","keywords":"ATP","year_from":2021,"year_to":2026,"page":1,"page_size":10,"open_access":true}
```

文献入库请求：

```json
{"pmid":"40487257","topic":"peroxisome","include_fulltext":true}
```

异步接口返回 HTTP 202 和 `job.id`，随后轮询 `/api/jobs/<id>`。入库结果的 `imported_type` 明确为 `fulltext` 或 `abstract`，同时提供来源摘要和警告。

检索 / 问答请求支持 `query`、`strategy`、`top_k`、`filters`、`paper_ids`。问答另支持 `context_neighbors`（0—2）和 `conversation_id`。引用包含原文、来源元数据与字符位置；回答的 `mode` 区分生成、部分支持、证据不足、摘录与模型失败降级。

回答质量接口使用 multipart 的 `bundle` 与 `reviews` 两个文件字段，不接受客户端的服务器文件路径。校验答卷与协议摘要后汇总；无评分时返回空指标，成功结果可通过历史导出接口下载。
