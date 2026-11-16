# 双语混合检索v2与同库留出题

结果与局限见[验收报告](../../docs/检索修复与留出题验收_20261116.md)。新题和运行前冻结协议见[留出题说明](../holdout-20261116/README.md)。

- `dev-summary.json`：原12题的新应用回归，10题生成、2题证据不足。
- `retrieval-summary.json`：原36条开发查询，混合检索中文17/18、英文17/18。
- `holdout-summary.json`：24题、三组72条运行记录，保留一次生成失败。
- `judge-dev-*`与`judge-dev-repeat-*`：同一12份答卷的两次裁判，11/12与12/12，不择优覆盖。
- `judge-holdout-*`：留出题评价协议、分组汇总与逐题判定；71份成功裁判、1份因生成失败未评分。
- `留出题逐题评价.md`：包含问题、组别、输出状态、评分与裁判依据。
- `holdout-retrieval-diagnostics.json`：登记参考资料与证据区间的实际上下文覆盖。
- `judge-repeatability.json`：12题重复裁判的逐维度一致性与分歧。
- `browser-report.json`、`workspace-report.json`：完整流程、双语及多尺寸验收；PowerShell执行策略限制明确登记。
- `verification.json`：冻结数据及关键代码摘要、浏览器状态核验。

完整答卷、裁判输入和原始响应位于本机`output/bilingual-v2/`。无RAG、基础RAG和优化RAG在留出题上的正确性满分数分别为21/23、6/24、10/24；无RAG另有一次调用失败。以上是模型自动评价，不是人工或专家结论。当前证据不支持RAG优于无RAG或零幻觉的结论。
