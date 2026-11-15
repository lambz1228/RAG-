# 细胞生物学综述语料 v1

2026年11月15日通过网络检索与Europe PMC官方接口取得30篇英文综述正文，六个主题各5篇，作为毕业设计知识库的扩充候选。每篇保留作者、期刊、年份、PMID、PMCID、DOI、许可、来源URL、原始XML摘要及内容范围。

直接查看[文献清单CSV](文献清单.csv)、[来源登记JSON](sources.json)和[导入与冻结摘要](corpus-summary.json)。本目录只发布来源清单与技术摘要，完整正文、原始响应和数据库保存在独立运行目录，可按下方命令重新取得。

## 范围与代表文献

| 主题 | 数量 | 本轮内容 | 代表文献 |
| --- | --- | --- | --- |
| 线粒体与能量代谢 | 5 | 动态、生物发生、自噬、ROS、呼吸链超复合体与代谢 | [Mitochondrial Respiratory Chain Supercomplexes: From Structure to Function](https://pmc.ncbi.nlm.nih.gov/articles/PMC9696846/) |
| 自噬与溶酶体 | 5 | 自噬类型、泛素调控、溶酶体功能及自噬体融合 | [The Biology of Lysosomes: From Order to Disorder](https://pmc.ncbi.nlm.nih.gov/articles/PMC9856021/) |
| 细胞信号转导 | 5 | MAPK、mTOR、翻译调控与mTORC2 | [Understanding MAPK Signaling Pathways in Apoptosis](https://pmc.ncbi.nlm.nih.gov/articles/PMC7177758/) |
| 内质网与蛋白质稳态 | 5 | UPR、IRE1、脂质代谢及泛素-蛋白酶体系统 | [The Unfolded Protein Response: An Overview](https://pmc.ncbi.nlm.nih.gov/articles/PMC8146082/) |
| 细胞周期与凋亡 | 5 | p53-p21-RB、CDK、生长因子、BCL2及凋亡通路 | [Cell cycle regulation: p53-p21-RB signaling](https://pmc.ncbi.nlm.nih.gov/articles/PMC9090780/) |
| 过氧化物酶体 | 5 | 生物发生、脂肪酸β氧化、过氧化氢、ABC转运及细胞器通讯 | [The peroxisome: an update on mysteries 3.0](https://pmc.ncbi.nlm.nih.gov/articles/PMC10822820/) |

## 检索与筛选口径

检索限定PubMed来源、综述、可获取PMC全文、首次发表日期2018—2025年；主题词限制在题名。各主题按被引次数排序最多返回100条，共584条主题记录、576个不同PMID。被引次数仅用于排列候选，不代表文章正确性或质量评分。

依据题名、摘要、基础机制覆盖和可取得性挑选36篇尝试获取；没有逐条评阅所有检索命中，不是系统综述。6篇虽然元数据标为CC BY，但XML未提供当前自动导入器要求的明确CC BY或CC0链接，单列于`sources.json`的`not_imported`，未混入本轮正文包。这并不表示它们没有合法许可或学术质量不合格。

最终30篇均核对官方PMID/PMCID与XML中的标识，全文许可包含明确CC BY链接。提取正文标题、段落和表格文字；不下载图像、补充材料，不把文末参考文献列表当作正文。保留原作者署名、原文链接及许可；提取、排版和规范化属于本项目的处理，不代表原作者认可本系统。

期刊记录年份与首次发表年份可能不同，清单保留官方`pubYear`字段；检索日期范围按`FIRST_PDATE`执行。XML摘要用于核对来源版本；重新建库的数据库SHA不保证与本机相同。

## 已完成的检查

- 30篇正文，0篇仅摘要；提取正文共1,905,947个字符。
- 在独立新库成功导入，生成3,281个分块，未覆盖此前7份资料的实验库与冻结题集。
- 本轮导入使用384维离线哈希向量，只验证处理链路；尚未为新语料运行真实Embedding或生成问答。
- 冻结数据库、上传JSON及60份原始来源记录，91个文件摘要核验通过。
- 人工学术复核尚未完成，未生成新题集、准确率或幻觉评价结果。

## 重新取得并导入

在仓库根目录安装项目依赖后运行现有采集工具，显式指定本目录登记表：

```bash
python scripts/collect_taskbook_sources.py --registry research/cell-biology-reviews-v1/sources.json --output data/cell-biology-reviews-v1
python -m biokb --data-dir data/cell-biology-reviews-v1/database serve
```

采集工具拒绝覆盖已有目录，并逐篇对照登记的XML SHA-256和全文类型。线上来源发生变化时保留新记录并报告差异；若要接受更新，需使用`--allow-source-updates`并另建新版本，重新冻结，不能沿用旧实验成绩。

配置真实Embedding后，应在新语料副本中重建索引，再编制对应题集与运行问答实验。此前7份资料上的实验结果不适用于这个30篇版本。

## 使用时的范围边界

本轮侧重动物和哺乳动物细胞机制，不能代表全部细胞生物学。信号部分以MAPK/mTOR为主，EGFR、膜运输、细胞骨架、染色质和植物细胞基础仍需补充。部分综述包含疾病与治疗讨论，应保留其适用条件，不把特定模型、组织或治疗结论泛化为所有细胞规律。

清单中的中文内容范围由助手依据题名摘要整理，不是原文翻译或专家评审结论。后续可围绕重点概念人工检查正文支持范围，补入教材式基础材料及必要的原始研究。

教材备选为[OpenStax Biology 2e](https://openstax.org/books/biology-2e/pages/preface)。当前官方前言标注CC BY-NC-SA 4.0，需遵守其非商业、署名和相同方式共享条件；本轮没有把它混入CC BY论文正文包，也没有将其自动采集功能写成已实现。
