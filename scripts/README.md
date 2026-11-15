# 当前主线工具

| 脚本 | 用途 |
| --- | --- |
| `collect_taskbook_sources.py` | 按登记PMID和来源摘要重建本轮开发语料 |
| `export_review_copy.py` | 导出保留来源定位与摘要的复核副本 |
| `freeze_corpus.py` | 冻结数据库与原始来源，核对文件摘要 |
| `prepare_taskbook_corpus.py` | 独立整理任务书开发语料与纳入排除记录 |
| `build_taskbook_dataset.py` | 开发题原文锚点、区间和库摘要核对 |
| `run_taskbook_retrieval.py` | 冻结副本上的真实检索对照 |
| `summarize_answer_reviews.py` | 汇总真实人工评分，空白保持待复核 |
| `summarize_experiment_run.py` | 完整问答运行的状态、回退与区间覆盖摘要 |
| `verify_answer_quality.py` | 评分材料上传、待复核显示与导出的界面回归 |
| `collect_biology.py` | 六个主题的官方文献采集，写入独立新库 |
| `reproduce_demo.py` | 核对样例摘要，离线重建两篇文献的流程演示 |
| `audit_questionbank.py` | 冻结题库完整性、题型缺口与待填写复核表 |
| `questionbank.py` | CSV题集校验与新版本冻结；不代填人工审核 |
| `run_answer_experiment.py` | 隔离副本问答对照与匿名盲评材料 |
| `derive_paper_metrics.py` | 从冻结检索报告复算论文级指标 |
| `verify_browser.py` | 本地上传、检索、问答、历史与任务流程 |
| `verify_workspace.py` | 双语、文献树、标签与多尺寸布局 |
| `verify_research.py` | 论文范围与证据定位 |
| `verify_literature.py` | 新采集界面的固定响应功能回归 |
| `verify_runtime.py` | wheel 与源码入口；应在安装wheel的独立环境运行 |
| `package_source.py` / `verify_package.py` | 源码分发包与摘要、路径、链接校验 |
| `biokb.py` | 未安装项目时的源码 CLI 入口 |
| `health_check.ps1` | Windows服务健康检查 |

旧版本专用采集、绘图与 GraphRAG 脚本见[归档分支](https://github.com/lambz1228/RAG-/tree/archive/pre-rebuild-20261115)。所有采集与实验使用独立目录；真实数据库、原始文献和旧实验不会随源码包分发。
