# 开放文献复现样例

本目录包含两篇文章的原始 Europe PMC XML 和转换后的正文段落 JSON，使用 CC BY 4.0 许可。题名、作者、DOI、原文 API、提取方式、文件 SHA-256 见 `manifest.json`。原始许可声明保留在 XML 中。

JSON只提取正文的段落，不包含图片、表格、补充材料和审稿附录。PMC13065327 存在多版本记录；此处固定取得的XML仅用于开发演示，不用于裁决版本关系或支持正式科研结论。

运行 `python scripts/reproduce_demo.py --output data/open-demo` 可离线验证来源摘要、入库、分块、检索和原文定位。脚本拒绝覆盖已有目录；生成的两道开发查询不是独立领域标准题，也不会自动获得人工评分。
