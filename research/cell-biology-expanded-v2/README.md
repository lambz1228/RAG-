# 细胞生物学扩充语料 v2

针对资料数量少、主题覆盖不足的问题，在原30篇综述基础上新增40篇综述，以及开放教材13章中的64个正文小节。合并得到134份资料：70篇综述论文、64个教材小节，覆盖18个主题，8,083个分块。教材小节数不计作论文数。

查看[134份语料清单](语料清单.csv)、[新增综述来源](review-sources.json)、[教材来源与逐模块摘要](foundation-sources.json)和[实际导入冻结摘要](corpus-summary.json)。原来的30篇登记仍保留在[综述v1](../cell-biology-reviews-v1/README.md)。

## 本次补齐的范围

| 层次 | 数量 | 内容 |
| --- | --- | --- |
| 已有综述 | 30篇 | 线粒体、自噬、信号、内质网、细胞周期和过氧化物酶体 |
| 新增综述 | 40篇 | 膜运输、细胞骨架、细胞核与染色质、DNA复制修复、基因表达、高尔基体与囊泡运输、发育信号、细胞连接及基质；每类5篇 |
| 教材正文 | 64小节 | 生物大分子、细胞结构、膜功能、代谢、细胞呼吸、光合作用、细胞通讯、细胞繁殖、减数分裂、DNA结构与功能、基因与蛋白、基因表达、生物技术与基因组学 |

教材用于建立基础概念，综述用于补充具体机制和适用语境。18个主题已接入网页文献采集入口，并提供英文标签；目录主题数量不代表每个细节已经覆盖。

例如，细胞骨架、主动与被动运输、DNA复制、转录翻译、酶与ATP等已有对应基础章节。核小体、核仁、膜蛋白运输、DNA修复、Wnt/Notch等另有专题综述。目录按来源元数据分类，不能解释成逐题可回答率或回答正确率。

## 来源与许可

新增综述通过Europe PMC官方接口取得，核对PMID、PMCID、明确CC BY许可、原始XML和正文摘要。依据题名摘要选择42篇尝试获取，40篇纳入，另2篇未满足本轮文章类型筛选规则，记录仍保留。部分期刊的XML通用类型为`research-article`，但PubMed记录和摘要明确标注为综述，原始字段原样保存，不据此伪造人工分类。

教材取自[OpenStax Biology 2e官方来源](https://github.com/openstax/osbooks-biology-bundle)，固定修订`5ae2f953cfe924469a6618578a00b3160439fc43`。主要作者为Mary Ann Clark、Jung Choi、Matthew Douglas；保留OpenStax与Rice University的来源信息、原始许可、前言和章节结构。

该教材层适用[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)，用于非商业用途，改编文本继续采用相同许可并保留署名。本文本提取移除了图片、表格、侧栏、习题、答案及脚注，保留正文和术语；数学排版转换为文本，图表与公式细节应回看原文。复合资料库包含该许可的教材文本，分发资料库时需保留相应许可条件；论文仍逐篇保留各自许可。源码仓库仅保存清单与工具，不分发上述全文和正式数据库。

资料为英文，尚未进行真实人工学术复核；中文提问的检索效果需要在真实语义索引上验证。章节正文和综述均有版本、物种、组织与实验条件的适用边界，不能因入库而认为其中每条陈述适用于所有细胞。

## 已完成的实际检查

- 134份资料全部导入独立新库：30篇原综述、40篇新增综述、64个教材小节；无重复、无导入失败。
- 生成8,083个片段，当前使用384维离线哈希向量，仅验证采集、解析与合并链路。
- 冻结357个数据库、上传文献及原始来源文件，摘要校验通过；旧7份资料与30篇语料的冻结库保留。
- 176项本地软件测试通过；18个主题的页面展示、双语及390/820/1440px布局通过浏览器检查。
- 新库没有执行真实Embedding或生成实验；未移植旧题集成绩，未生成准确性或幻觉抑制结论。

## 从清单重新取得完整语料

在仓库根目录安装项目依赖后，以下命令分别取得原综述、新综述、教材，并合并到独立新库。各输出目录应尚不存在。

```bash
python scripts/collect_taskbook_sources.py --registry research/cell-biology-reviews-v1/sources.json --output data/reviews30
python scripts/collect_taskbook_sources.py --registry research/cell-biology-expanded-v2/review-sources.json --output data/reviews40
python scripts/collect_foundation_corpus.py --output data/foundation64
python scripts/prepare_expanded_corpus.py --sources data/reviews30 data/reviews40 data/foundation64 --output data/cell-biology-v2
python -m biokb --data-dir data/cell-biology-v2/database serve
```

论文采集逐篇对照XML摘要与全文类型。线上资料变更时保留差异，不沿用旧快照标识；接受更新应另建版本，重新冻结与评价。教材固定提交和逐文件摘要必须一致；`--source-cache`可读取已有原始CNXML缓存，仍执行完整摘要核对。

合并工具保留各来源目录，在新库归档原始响应；明确使用离线哈希向量，不读取已有模型密钥或覆盖当前`.env`。启动网页时按本机现有配置运行；如配置了其他Embedding，应先在新副本中重建索引。

配置真实向量服务后，可以创建独立语义版本：

```bash
python -m biokb create-revision data/cell-biology-v2/database data/cell-biology-v2-semantic
python -m biokb --env-file config/local-models.env.example --data-dir data/cell-biology-v2-semantic reindex
```

新题集与问答实验应绑定这个新版本的资料和向量指纹。基础章节与研究综述的比例、主题深度和适用物种仍可继续按实际使用问题调整。
