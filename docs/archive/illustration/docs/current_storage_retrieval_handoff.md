# 当前系统存储与检索对接表

更新时间：2026-08-23

本文基于当前代码实现整理，供开发同事对接科研绘图系统的数据存储、数据入口和检索链路时快速定位。

## 一、总览

| 对接主题 | 当前实现 | 是否运行中默认启用 | 主要代码入口 | 主要数据位置 |
|---|---|---:|---|---|
| 应用运行态结构化数据 | SQLite | 是 | `sci-illust-system/web_app/database.py` | `sci-illust-system/web_app/data/knowledge.db` 或 `SCI_WEBAPP_DB_PATH` |
| `Repository 抽象层` | SQLite 单实现 | 是 | `sci-illust-system/web_app/repositories.py` | SQLite 文件 |
| 文本知识库工作区 | GraphRAG 工作区文件 | 是 | `sci-illust-system/text_kb/graphrag_manager.py` | `sci-illust-system/web_app/data/text_kb/<domain>/` |
| 关键词/术语召回 | 进程内轻量向量索引 + 关键词匹配 | 是 | `sci-illust-system/knowledge_base/kb_core.py` | `domain_vocab.json` + `corpus/` |
| 图片资产文件 | 本地文件目录 | 是 | `LocalImageAssetStorage` / `ElementCatalog` | `SCI_IMAGE_ASSET_ROOT` 或数据目录 |
| 图片映射检索 | `jsonl` 映射文件 + 聚合服务 | 是 | `sci-illust-system/knowledge_base/element_catalog.py` | `output_kb/terms.jsonl` 等 |
| Bioicons 外部素材库 | 本地素材目录 | 视环境而定 | `knowledge_base/bioicons_library.py` | `BIOICONS_ROOT` |
| MySQL 三逻辑库 | 未接入 | 否 | — | — |

## 二、按模块对接

| 模块 | 负责内容 | 当前存储方式 | 当前检索方式 | 入口 API / 服务 | 对接注意事项 |
|---|---|---|---|---|---|
| `entries` 图元术语库 | 中文名、英文名、领域、分类、标签、描述 | SQLite `entries` 表 | `KnowledgeBase.query()` + `CatalogService` | `/api/entries` | 当前 UI 和服务层默认依赖 Repository，不要直接绕过服务层写表 |
| `documents` 文档库 | 上传文本、路径、内容、向量化状态 | SQLite `documents` 表 | 文档列表来自 Repository，语义检索不直接查该表 | `/api/document/*` | `documents.vectorized` 只是状态位，不代表系统内置向量库存储已经完整落库 |
| `settings` 系统配置 | 模型地址、默认模型、API Key 等 | SQLite `settings` 表 | 按键读取，不走搜索 | `/api/ollama/*` | 配置读取已被运行时逻辑包装，不建议直接手工读写 DB |
| 文本知识库 `text_kb` | 清洗文本、输入文本、GraphRAG 输入输出 | 文件目录 | GraphRAG CLI `index/query` | `/api/search`、`/api/text-kb/status` | 这是文件工作区，不是主库表；索引前依赖 embedding 配置 |
| 轻量知识库 `KnowledgeBase` | 领域词表、术语、语料 snippets | JSON + 目录文本，进程启动后载入内存 | 自定义 `TextEmbedding` + `VectorStore` + keyword 匹配 | `/api/search` | 这是轻量本地召回，不是 FAISS/Milvus/ES |
| 图片库 `image_library` | 图片映射、SVG、预览资产 | `jsonl` 映射 + 本地文件 | `ElementCatalog` 聚合匹配 + Bioicons + 元素建议 | `/api/image-library/*` | 当前图片库不是数据库驱动，而是文件索引驱动 |
| 图片关系图 | 图元关系、测试态图谱 | 内存仓库 `InMemoryImageGraphRepository` | 仅服务内访问 | `ImageLibraryService` | 当前不是持久化图数据库，重启后不保留 |

## 三、当前默认运行链路

| 场景 | 实际链路 | 说明 |
|---|---|---|
| 页面打开加载基础术语库 | `app.py` -> `build_repository()` -> `SQLiteKnowledgeRepository` | 默认环境下直接连 SQLite |
| 关键词检索 | `SearchService.search()` -> `KnowledgeBase.query()` | 返回术语命中、关键词命中、语料 snippets |
| 文本问答/语义检索 | `SearchService.search()` -> `GraphRAGTextKBManager.query()` | 依赖 GraphRAG CLI 和已构建索引 |
| 查看图元条目 | `/api/entries` -> `CatalogService` -> Repository | 结构化条目走数据库表 |
| 浏览图片库 | `/api/image-library/browse` -> `ImageLibraryService` -> `ElementCatalog` | 从 `jsonl` 映射和本地 SVG 中取 |
| 获取图片预览文件 | `/api/image-library/asset/<img_id>` -> `resolve_asset_file()` | 最终读取本地文件路径 |

## 四、核心代码入口对照

| 代码文件 | 作用 | 关键点 |
|---|---|---|
| `sci-illust-system/web_app/app.py` | 运行时装配入口 | 初始化 `kb`、`db`、`text_kb`、`element_catalog`、`image_storage` |
| `sci-illust-system/web_app/database.py` | SQLite 原始数据层 | 默认建 `entries/documents/settings` 三张表 |
| `sci-illust-system/web_app/repositories.py` | Repository 抽象层 | `build_repository()` 决定走 SQLite |
| `sci-illust-system/web_app/services/search_service.py` | 搜索编排层 | 同时调用轻量 KB 与 GraphRAG |
| `sci-illust-system/knowledge_base/kb_core.py` | 本地词表与轻量向量检索 | 自定义 embedding 与内存向量表 |
| `sci-illust-system/text_kb/graphrag_manager.py` | GraphRAG 工作区管理 | 管理 `incoming/raw/cleaned/input/output/cache` |
| `sci-illust-system/knowledge_base/element_catalog.py` | 图片映射文件读取 | 读取 `terms.jsonl`、`image_element_map.jsonl`、`doc_mentions.jsonl` |
| `sci-illust-system/web_app/services/image_library_service.py` | 图片库聚合服务 | 聚合映射文件、元素建议、Bioicons |
| `docs/current_storage_retrieval_handoff.md` | 本地存储与检索架构 | 说明 SQLite、文件工作区、轻量 KB 和图片库的链路 |

## 五、环境变量对接

| 变量名 | 作用 | 当前默认含义 |
|---|---|---|
| `SCI_REPOSITORY_KIND` | Repository 类型 | 默认 `sqlite` |
| `SCI_WEBAPP_DB_PATH` | SQLite 数据库文件路径 | 默认 `sci-illust-system/web_app/data/knowledge.db` |
| `SCI_FOCUS_DOMAIN` | 当前领域 | 默认 `biology` |
| `SCI_OUTPUT_KB` | 图片映射输出目录 | `ElementCatalog` 优先从这里读取 |
| `SCI_IMAGE_ASSET_ROOT` | 图片资产根目录 | 本地图像/SVG 文件目录 |
| `BIOICONS_ROOT` | Bioicons 素材库目录 | 本地素材目录 |
| `GRAPHRAG_API_BASE` | GraphRAG LLM 接口 | GraphRAG 配置用 |
| `GRAPHRAG_EMBEDDING_API_BASE` | GraphRAG embedding 接口 | 索引前必须可用 |

## 六、当前实现边界

| 项目 | 当前状态 | 说明 |
|---|---|---|
| SQLite 主库 | 已稳定使用 | 当前真实运行主存储 |
| 文本向量库持久化 | 未独立落专用向量库 | 当前 GraphRAG 自管工作区；轻量 KB 只在内存 |
| 图数据库 | 未接入正式持久化图库 | 图片关系当前是内存仓库 |
| 对象存储 | 未接入 | 图片资产目前走本地目录 |
| 图片库数据库化 | 未完全完成 | 当前仍以 `jsonl` 映射文件为主 |

## 七、建议给对接同事的结论

| 结论项 | 建议 |
|---|---|
| 如果只是读取当前本地运行数据 | 先按 `SQLite + 文件工作区 + 本地图片目录` 理解 |
| 如果要接文本检索 | 区分两条链：轻量 `KnowledgeBase` 召回，和 `GraphRAG` 问答检索 |
| 如果要接图片库 | 先读 `ElementCatalog` 和 `ImageLibraryService`，不要只盯数据库 |
| 如果要导入清洗后的数据 | 图片与文本应分层导入，不建议直接混写到单一表结构 |

