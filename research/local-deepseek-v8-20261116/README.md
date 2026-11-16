# Windows本机DeepSeek v8开发验证

实际运行日期：2026年11月16日。结果和局限见[完整报告](../../docs/本机DeepSeek回归与自动评价结果.md)，环境和方法见[运行说明](../../docs/本机DeepSeek运行与自动评价.md)。

- `index-summary.json`：134份资料、8,083个真实E5向量、冻结库和清单摘要。
- `retrieval-summary.json`：本机36条中英成对开发查询的指定来源召回。
- `deployment-summary.json`、`taskbook-summary.json`：12份应用回归与三组36份对照的运行诊断。
- `judge-deployment-*`、`judge-taskbook-*`：独立模型自动评价协议、分组汇总和逐题判定；不含人工评分。
- `deployment-逐题评价.md`、`taskbook-逐题评价.md`：可阅读的逐题分数和裁判依据。
- `environment.json`：本机Python与关键依赖版本。
- `frozen-verification.json`、`web-smoke.json`：文件摘要与Web API检查结果。

完整答卷、裁判输入、原始响应、依赖清单和Web问答冒烟记录保存在本机`output/local-deepseek-v8/`。完整来源与数据库保存在`data/local-20261116/`，模型在`data/models/multilingual-e5-small/`。不分发正文与模型权重。

题目仍为原有开发草稿，不按本轮结果修改。生成模型为`deepseek-flash`，裁判为`deepseek-v4-pro`，同属DeepSeek系列；评价结果不能当作专家确认。无RAG本轮12/12正确且完整，优化RAG为11/12，因此本轮不支持RAG提升正确率或降低幻觉的结论。人工评价可选，不阻塞上述流程。
