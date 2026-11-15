# 第三方来源与设计参考

BioKB 的知识管理布局与“文献发现 → 入库 → 检索调试 → 证据问答”流程参考 [MimirQ](https://github.com/skygazer42/MimirQ)，参考版本 `5d08b54beb8011a17ce274319fb79a5f121568c9`。MimirQ 作者为 skygazer42，采用 Apache License 2.0。

本次在既有 BioKB 后端上独立实现界面与文献采集模块，没有复制 MimirQ 的源码、品牌图标、截图、DeepDoc、模型权重或第三方资源。论文应如实说明其界面与流程参考来源，不将参考设计或通用 RAG 方法描述为原创算法。

示例文献的作者、标题、DOI、Europe PMC 原文接口、CC BY 4.0 许可与原始 XML 摘要保存在 `examples/open_demo/manifest.json`。转换后的 JSON 只提取正文段落，不包含图片、表格、补充材料和审稿附录。文献内容仍由原作者拥有著作权；按文章许可署名与使用。

Python 依赖由 `pyproject.toml` 声明，使用各自许可。模型由用户自行配置或下载，不随仓库分发。

本地开发实验使用官方 Qwen3-Embedding-0.6B-GGUF 与 Qwen2.5-1.5B-Instruct-GGUF，模型仍适用各自上游许可。仓库仅记录模型标识、修订与文件摘要，不分发权重；来源见 `research/taskbook-dev-v1/model-provenance.json`。运行时为 llama.cpp，其发布包摘要已与官方发布记录核对。
