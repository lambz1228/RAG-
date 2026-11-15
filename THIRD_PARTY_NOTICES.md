# 第三方许可与资料来源

示例文献的作者、标题、DOI、Europe PMC 原文接口、CC BY 4.0 许可与原始 XML 摘要保存在 `examples/open_demo/manifest.json`。转换后的 JSON 只提取正文段落，不包含图片、表格、补充材料和审稿附录。文献内容仍由原作者拥有著作权；按文章许可署名与使用。

Python 依赖由 `pyproject.toml` 声明，使用各自许可。模型由用户自行配置或下载，不随仓库分发。

教材基础层取自OpenStax / Rice University的Biology 2e，主要作者为Mary Ann Clark、Jung Choi、Matthew Douglas。登记的官方源修订适用CC BY-NC-SA 4.0；提取正文、移除图片表格及练习、转为文本的处理已注明，教材文本继续按该许可用于非商业用途。来源、逐模块摘要和完整原始许可的摘要见 `research/cell-biology-expanded-v2/foundation-sources.json`，本仓库不分发教材正文或模型权重。

本地开发实验使用官方 Qwen3-Embedding-0.6B-GGUF 与 Qwen2.5-1.5B-Instruct-GGUF，模型仍适用各自上游许可。仓库仅记录模型标识、修订与文件摘要，不分发权重；来源见 `research/taskbook-dev-v1/model-provenance.json`。运行时为 llama.cpp，其发布包摘要已与官方发布记录核对。
