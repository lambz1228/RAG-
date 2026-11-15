# 基于 RAG 技术的生物领域知识库系统研发

BioKB 是毕业设计项目，围绕生物领域文献的管理、检索、带引用的知识问答与实验评估展开。主线代码采用 Python、Flask、SQLite 和原生 JavaScript，支持本地运行。

## 已实现能力

- 文献入库：TXT、Markdown、PDF、DOCX、CSV、结构化 JSON；原始文件 SHA-256 去重与归档。
- 文本处理：规范化、带重叠分块、原文位置保存与来源元数据。
- 科研论文目录：按一致的 PMID、PMCID、DOI 关联摘要与全文；按论文筛选，选择单篇或多篇限定研究范围，返回每个资料版本的原文入口。
- 知识检索：BM25、向量余弦相似度、RRF 混合检索、混合召回后重排序；支持主题、年份与资料类型筛选。
- 模型接口：离线哈希向量，以及可配置的 Ollama / OpenAI 兼容 embedding 与生成接口。
- 证据问答：原文摘录、生成回答、部分支持、证据不足和失败降级分别标记；支持相邻分块扩展，保留召回中心、原文偏移、论文范围与证据快照。
- 工程能力：异步入库/问答/索引任务、进度与错误状态持久化、重启中断标记与重新提交、问答历史和 JSON 导出。
- 实验能力：文档级 Hit@K、Recall@K、AllHit@K、MRR@K、Precision@K、nDCG@K 与延迟分位数；记录数据集摘要和索引配置。可对冻结候选排名另行复算论文级指标，保留两种口径。
- 双语工作台：功能导航、文献树、可关闭标签页与页面目录；中文/English 即时切换并记住选择，支持手机折叠导航。最左侧独立图标栏已移除。

默认不需要在线模型即可验证文献与检索流程。默认哈希向量属于离线基线，原文摘录不属于 LLM 生成。正式语义检索与生成式 RAG 实验需配置并验证真实模型。

本工作区的 2.1 真实模型联调使用独立的 `data/biokb-v21-semantic/`，模型为 Qwen3-Embedding-8B、Qwen2.5-72B-Instruct 和 bge-reranker-v2-m3；默认 `hybrid_rerank` 由开发集选定。当前 2.2 工程版本增加论文与证据工作流，原有语义库未覆盖。2026-11-05 的 v22 独立库补充 38 篇开放全文，v23 补充 33 篇 2021—2024 年全文；v24、v25 再为 15 个缺口组合各补充一篇，v26 独立修复 47 篇全文的作者字段。当前扩充版有 221 篇归并论文、89 篇有全文，另有 132 篇仅摘要。来源、许可、版本及科研限制见[文献扩充记录](docs/文献扩充记录.md)、[历史文献扩充记录](docs/历史文献扩充记录.md)和[缺口文献扩充记录](docs/缺口文献扩充记录.md)。密钥只引用系统变量，分发模板仍默认离线模式。既有实验结果与局限分别见 [2.1 优化验收](docs/2.1优化验收.md)和[科研证据整改验收](docs/科研证据整改验收.md)。

2026-11-05 的[v26 科研质量机器审计与实验报告](docs/科研质量验收报告_v26.md)记录了正式 v26 库的来源链核验、检索与真实模型问答诊断，以及 132 篇仅摘要论文的逐篇获取状态；科研质量尚未放行，独立领域题集和人工复核仍缺失。[v21 历史验收报告](docs/科研质量验收报告.md)仅用于旧索引对照。整改依赖和建议门槛见[科研质量优化计划](docs/科研质量优化计划.md)。

## 快速启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# 仅首次创建，已有 .env 时不要覆盖：
Copy-Item .env.example .env
.\.venv\Scripts\python.exe app.py
```

访问 http://127.0.0.1:5000 。也可使用 `start.bat`。新安装默认将数据保存到 `data/biokb/`；本机已有 `.env` 时以其中配置为准，不覆盖原库。

使用已扩充的独立语义库时运行 `.\start-expanded.bat`；脚本只在当前进程选择 `data/biokb-v26-authors-20261105/`，并先检查数据库是否存在，不修改 `.env`。v22 至 v25 均保留，可通过 `python -m biokb --data-dir <独立库目录> serve` 单独启动。

已有生物语料迁移：

```powershell
.\.venv\Scripts\python.exe -m biokb import-legacy
.\.venv\Scripts\python.exe -m biokb doctor
.\.venv\Scripts\python.exe -m pytest -q
```

迁移只读取旧语料，写入独立新库；已有旧数据库和 GraphRAG 索引保留。

## 目录结构

```text
src/biokb/              主线后端、Web 页面与静态模块
  config.py            配置与校验
  storage.py           SQLite 数据访问
  ingestion.py         文档解析、切分与入库
  providers.py         向量和生成模型适配
  quality.py           资料质量与筛选
  papers.py            论文身份归并与研究范围解析
  revisions.py         独立版本复制
  reranking.py         重排序适配
  retrieval.py         四种检索策略与有界查询向量缓存
  rag.py               证据问答与引用校验
  evaluation.py        独立标准出处评测
  jobs.py              有界后台任务
  web/                 应用工厂、页面、前端模块
tests/biokb/           当前主线自动化测试
docs/                 当前架构、接口、开发、部署和实验文档
docs/archive/         旧项目 Markdown 原文归档
scripts/biokb.py       源码 CLI 入口
data/biokb/            保留的原始 2.0 数据库
data/biokb-v21/        2.1 同语料哈希对照库
data/biokb-v21-semantic/  2.1 历史语义索引库（数据均不进入源码包）
data/biokb-v22-expanded-20261105/  独立扩充版语义库
data/research_corpus/expansion_20261105/  采集 XML、筛选副本和入库核查清单
data/biokb-v23-historical-20261105/  2021—2024 年文献扩充版语义库
data/research_corpus/historical_20261105/  历史文献 XML、元数据快照及核验报告
data/biokb-v24-gapfill-20261105/  14 篇缺口全文的独立语义库
data/biokb-v25-peroxisome-20261105/  补齐 2024 年 peroxisome 的独立语义库
data/biokb-v26-authors-20261105/  作者字段修复后的本机扩充版
data/research_corpus/gapfill_20261105/  14 篇全文的发现、原文和核验快照
data/research_corpus/peroxisome_targeted_20261105/  定向补采与核验快照
sci-illust-system/     原始知识数据及历史 RAG 复现依赖
```

## 使用与文档

- [使用说明书](使用说明书.md)
- [项目介绍](项目介绍.md)
- [安装指南](SETUP_GUIDE.md)
- [架构设计](docs/架构设计.md)
- [API 说明](docs/API说明.md)
- [开发说明](docs/开发说明.md)
- [部署文档](docs/部署文档.md)
- [实验设计](docs/实验设计.md)
- [重构迁移说明](docs/重构迁移说明.md)
- [验证记录](docs/验证记录.md)
- [双语工作台验收](docs/双语工作台验收.md)
- [科研证据整改验收](docs/科研证据整改验收.md)
- [v26 科研质量机器审计与实验报告](docs/科研质量验收报告_v26.md)
- [v21 历史科研质量验收报告](docs/科研质量验收报告.md)
- [科研质量优化计划](docs/科研质量优化计划.md)
- [文献扩充记录](docs/文献扩充记录.md)
- [历史文献扩充记录](docs/历史文献扩充记录.md)
- [缺口文献扩充记录](docs/缺口文献扩充记录.md)
- [文件清理记录](docs/文件清理记录.md)

## 实现边界

当前面向单机知识库：SQLite 持久化向量，进程内执行相似度计算，最近 100 条任务持久化，重启后未完成任务标记中断，支持重新提交。扫描 PDF 需要先 OCR。本机资料仍以摘要为主，自动质量状态尚未由生物领域专家复核；论文归并、来源编号和引用编号检查均不能证明回答事实正确。旧 GraphRAG 实验保留为历史研究材料，没有冒充当前默认检索策略。

配置文件和源码包不包含真实模型密钥。对外监听必须配置访问令牌，生产部署还需 TLS 和完整的身份权限管理。旧绘图代码、测试和演示已移出工作区并在项目外备份；当前启动和测试面向新的 RAG 知识库，原始数据与实验结果保持原路径。
