# 脚本入口

当前主线：

- `biokb.py`：未安装包时使用完整 CLI。
- `package_source.py`：打包新知识库源码，排除原文、数据库、凭据和历史模块。
- `health_check.ps1`：检查新应用健康接口和页面。
- `verify_browser.py`：临时知识库的浏览器完整流程和移动端验收。
- `verify_workspace.py`：双语切换、文献树分页、原文阅读、标签页、快捷键和多种宽度布局验收；`--snapshot-data` 先只读复制数据库，再在临时副本中截图。
- `verify_package.py`：检查源码包清单、摘要、排除规则和 Markdown 链接。
- `verify_preservation.py`：根据已登记的原数据基线复核 SHA-256。
- `verify_runtime.py`：使用已安装 wheel 的独立解释器验证 CLI、源码入口和桌面后端。
- `map_legacy_sources.py`：根据历史文件名规则和完整原文核验生成出处映射。
- `audit_research_corpus.py`：只读审计当前数据库的论文版本、来源、内容类型和人工复核缺口；输出数据库摘要。
- `audit_research_quality.py`：只读比对正式标识、原始全文、冻结问答引用与字符偏移，生成科研质量摘要、逐题证据包和空白人工复核表；`--verify-official` 可核对 Europe PMC 当前元数据。
- `derive_paper_metrics.py`：核对冻结报告与当前语料、索引和向量指纹，在既有候选片段上按论文归并复算检索指标；不调用模型或改写旧实验。
- `replay_research_cases.py`：在语义库独立临时副本上复测两道既有失败题，不写入正式库。
- `verify_research.py`：临时知识库的科研论文范围、证据定位与中英文页面验收。
- `expand_open_corpus.py`：按主题采集 Europe PMC 开放许可全文，保留 XML、JSON、许可和拒收记录；只读核对父库。
- `collect_seeded_corpus.py`：在官方检索接口故障时，按已保存候选 PMCID 清单逐篇获取官方全文 XML，并按主题与年份配额采集。
- `discover_gap_candidates.py`：以 Crossref DOI 检索和 PMC ID Converter 为未填满的主题年份建立候选清单，保留接口原始响应和摘要；单组缺口支持定向题名条件。
- `prepare_gap_retry.py`：从冻结候选、拒收及网络失败记录生成独立补采清单，复核原始接口快照摘要。
- `fetch_pubmed_metadata.py`：保存 PubMed EFetch XML 和 PMC 标识转换响应，核对 PMID、PMCID、DOI 及快照重算结果。
- `curate_open_corpus.py`：用官方元数据及原始 XML 复核采集批次，依据主题排除清单生成待入库副本。
- `import_expanded_corpus.py`：逐篇导入独立语义库，核验模型指纹并保存可续跑的入库清单。
- `enrich_corpus_authors.py`：从已登记的 JATS XML 为独立子版本补齐作者元数据，并核验父库资料、上传原件和向量保持一致。
- `verify_expanded_index.py`：只读核对新旧库文档、全文、来源标识、上传原件、分块及向量。
- `verify_expanded_api.py`：先只读复制正式库，在临时副本上核对论文目录、标识、年份与题名检索入口。
- `verify_pubmed_snapshot.py`：从 PubMed 原始 XML 重算标识、文献类型及更正关系，核对采集批次的元数据快照。

本机文献扩充的实际批次、命令和限制见[文献扩充记录](../docs/文献扩充记录.md)、[历史文献扩充记录](../docs/历史文献扩充记录.md)及[缺口文献扩充记录](../docs/缺口文献扩充记录.md)。原始 XML、筛选副本及语义数据库均位于 `data/`，不进入源码包。

2.1 新增研究工具：

- `check_models.py`：真实接口小样本联调，无凭据输出。
- `prepare_research_dataset.py`：按共享标准出处分组，保留原题摘要和待人工审核字段。
- `fetch_open_fulltexts.py`：下载符合开放访问条件的 Europe PMC XML，保留原始 XML、许可与下载报告。
- `run_research_experiments.py`：同语料哈希/真实模型对照，先开发选择，再冻结评测。
- `prepare_answer_review.py`：新增全文题、真实问答和人工复核材料；不填写专家评分。

研究脚本的输入语料不随源码包分发，运行前按 `--help` 指定实际路径；真实接口脚本读取本机配置。

研究复现保留：`fetch_europepmc_corpus.py` 用于历史语料采集，`rebuild_text_kb.py` 用于旧 GraphRAG 工作区维护，`rag_eval/` 保留既有云模型与 GraphRAG 实验实现。这些脚本仍依赖保留的 `sci-illust-system/text_kb/`、`embedding/` 和独立模型配置；执行前应核对数据目标与模型费用。当前主线实验使用 `python -m biokb evaluate`。

旧绘图 QA、SVG 示例、汇报构建脚本及 `start_legacy.py` 已归档移出工作区，清单见 `docs/文件清理记录.md`。
