# 第三方许可与资料来源

示例文献的作者、标题、DOI、Europe PMC 原文接口、CC BY 4.0 许可与原始 XML 摘要保存在 `examples/open_demo/manifest.json`。转换后的 JSON 只提取正文段落，不包含图片、表格、补充材料和审稿附录。文献内容仍由原作者拥有著作权；按文章许可署名与使用。

[细胞生物学补充文献](research/cell-biology-literature-supplement/README.md)中的中文论文仅收录题录。英文开放综述条目为中文归纳笔记，按 CC BY 4.0 署名，并在 JSON 中保留原作者、原文 DOI、许可链接及所依据的摘要或章节。第二批来源与筛选记录见[检索清单](research/cell-biology-literature-supplement/expansion-search.json)。

Python 依赖由 `pyproject.toml` 声明，使用各自许可。模型由用户自行配置或下载，不随仓库分发。

教材基础层取自OpenStax / Rice University的Biology 2e，主要作者为Mary Ann Clark、Jung Choi、Matthew Douglas。登记的官方源修订适用CC BY-NC-SA 4.0；提取正文、移除图片表格及练习、转为文本的处理已注明，教材文本继续按该许可用于非商业用途。来源、逐模块摘要和完整原始许可的摘要见 `research/cell-biology-expanded-v2/foundation-sources.json`，本仓库不分发教材正文或模型权重。

本地开发实验使用官方 Qwen3-Embedding-0.6B-GGUF 与 Qwen2.5-1.5B-Instruct-GGUF，模型仍适用各自上游许可。仓库仅记录模型标识、修订与文件摘要，不分发权重；来源见 `research/taskbook-dev-v1/model-provenance.json`。运行时为 llama.cpp，其发布包摘要已与官方发布记录核对。

真实语义版采用intfloat/multilingual-e5-small的官方ONNX量化文件（MIT许可），按官方模型卡使用query/passage角色前缀、注意力掩码平均池化和归一化。生成使用Qwen2.5-3B-Instruct-GGUF，适用上游Qwen RESEARCH LICENSE；本轮用于毕业设计非商业研究。固定修订、逐文件摘要、许可链接和运行时版本见 `research/semantic-dev-v1/model-provenance.json`，权重不随源码分发。
