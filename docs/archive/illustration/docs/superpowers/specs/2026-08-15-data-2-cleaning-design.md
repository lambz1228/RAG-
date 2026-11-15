# data_2 强力清洗设计

**目标**

为 `D:\ljn-xm\data_2.zip` 建立一套可重复执行、保留原始输入、输出清洗产物与审计报告的强力数据清洗流程，为后续知识库导入打基础。

**范围**

- 输入只读：不修改 `data_2.zip`，不覆盖原始解压内容。
- 输出分层：清洗后的数据、异常记录、统计摘要、验证结果分别输出到固定目录。
- 本轮只做数据清洗与清洗验证，不直接执行正式导入。
- 清洗对象覆盖：
  - 图元词表与元素导出
  - 文献标准化正文与文献导出
  - 元素到文献映射
  - 知识图谱实体、关系、三元组

## 背景与现状

当前系统主运行知识库仍以 `sci-illust-system/web_app/data/knowledge.db` 和 `entries/documents/settings` 结构为主，服务器版 MySQL 已定义更完整边界，但还没有直接承载本次数据包全部结构的稳定模型。

`data_2.zip` 不是单一数据表，而是一个科研绘图知识包，包含：

- `data/domain_vocab.json`
- `data/kb_exports/kb_a_elements.json`
- `data/kb_exports/kb_b_literature.jsonl`
- `data/kb_exports/element_literature_map.json`
- `data/normalized/*.json`
- `data/kg_output/entities.json`
- `data/kg_output/relations.json`
- `data/kg_output/triples*.json`

抽样检查发现以下质量风险：

- 文献正文混入 HTML 标签，如 `h4`
- 部分中文文件名存在编码异常迹象
- 图谱实体中存在明显噪声，如 `h4`
- 图谱关系中存在半句截断、主宾语残缺
- 元素、文献、映射、图谱四层之间存在键不一致和结构缺口

## 设计原则

1. 原始输入不可变

- 清洗流程只读取 `data_2.zip`
- 所有结果写入 `build/` 目录
- 所有衍生文件可删除并重建

2. 幂等与可重复执行

- 相同输入、相同参数下，输出目录结构和统计口径保持稳定
- 文件命名、排序、去重策略保持确定性
- 异常记录使用稳定分类和稳定主键

3. 清洗与审计分离

- 清洗结果不等于导入结果
- 所有删除、过滤、归一、修正规则都要有统计和异常记录

4. 强力清洗但保守保源

- 可以过滤明显坏数据
- 不凭空创造学术内容
- 无法可靠修复时，记录异常并剔除出可导入层

## 输出目录契约

固定输出到以下目录：

```text
build/
├── data_2_cleaned/
│   ├── elements/
│   │   ├── domain_vocab.cleaned.json
│   │   ├── kb_a_elements.cleaned.json
│   │   └── element_index.cleaned.json
│   ├── literature/
│   │   ├── normalized.cleaned.jsonl
│   │   ├── kb_b_literature.cleaned.jsonl
│   │   ├── literature_index.cleaned.json
│   │   └── texts/
│   ├── mappings/
│   │   └── element_literature_map.cleaned.json
│   └── kg/
│       ├── entities.cleaned.json
│       ├── relations.cleaned.json
│       ├── triples.cleaned.json
│       └── alias_index.cleaned.json
└── data_2_reports/
    ├── cleaning_summary.json
    ├── validation_summary.json
    └── exceptions.jsonl
```

## 清洗流水线

### 1. 元素层清洗

**输入**

- `data/domain_vocab.json`
- `data/kb_exports/kb_a_elements.json`

**目标**

- 生成一套稳定的元素主索引
- 统一元素主键、名称、领域、分类、形状、标签
- 为后续文献映射和图谱归一提供锚点

**规则**

- `element_id` 优先使用原始 `element_id`
- 若缺失 `element_id`，基于规范化英文名或中文名生成稳定 ID
- 中文名、英文名两端空白清理
- `domain/category/shape` 执行规范化映射
- `tags` 去空值、去重、排序
- `color_scheme` 只保留合法颜色值
- 对重复元素执行合并：
  - 同 `element_id` 合并
  - 若 `element_id` 缺失，则按规范化 `english` 或 `name` 合并
- 保留来源字段，记录合并来源数

**过滤条件**

- `name` 与 `english` 同时为空
- 关键字段明显乱码且无法回退到原始有效名称
- 重复记录完全被上位记录覆盖

**输出**

- `domain_vocab.cleaned.json`
- `kb_a_elements.cleaned.json`
- `element_index.cleaned.json`

### 2. 文献层清洗

**输入**

- `data/normalized/*.json`
- `data/kb_exports/kb_b_literature.jsonl`

**目标**

- 建立可用于后续知识库导入的稳定文献正文层
- 标准化 DOI、URL、标题、年份、作者、关键词
- 生成可落地文件名和文本正文

**规则**

- 优先保留原始文献标识：`id`、`doi`、`url`
- `title` 清理控制字符、异常空白和文件名非法字符
- `content` 去 HTML 标签，转为纯文本
- `content` 统一换行、压缩多余空白、清除空段落
- `doi` 转小写、去前后空白、统一 `https://doi.org/` 前缀外观
- `url` 校验协议，仅保留 `http/https`
- `authors/keywords/matched_terms` 统一为去空值后的列表
- `year` 转整数；无法解析时记异常
- 建立文献去重键，优先级：
  - DOI
  - URL
  - `source + id`
  - 规范化标题
- 重复文献合并时保留正文更完整、元数据更全的一条

**过滤条件**

- 标题为空且正文为空
- DOI、URL、标题、正文都不能作为稳定识别依据
- 文本清洗后正文为空

**输出**

- `normalized.cleaned.jsonl`
- `kb_b_literature.cleaned.jsonl`
- `literature_index.cleaned.json`
- `texts/*.txt`

### 3. 映射层清洗

**输入**

- `data/kb_exports/element_literature_map.json`
- 元素层清洗产物
- 文献层清洗产物

**目标**

- 只保留“有效元素 -> 有效文献标识”的映射
- 输出可直接参与后续导入设计的映射结果

**规则**

- `element_id` 必须能命中清洗后的元素主索引
- 文献链接项标准化为两类：
  - DOI
  - URL
- 无法识别为合法 DOI/URL 的映射项记异常
- DOI 映射优先通过 DOI 命中文献层
- URL 映射优先通过 URL 命中文献层
- 同一元素的重复引用去重并排序
- 输出每个元素映射到的有效文献数量

**过滤条件**

- 指向无效元素
- 指向非法 DOI/URL
- 指向清洗后不存在的文献

**输出**

- `element_literature_map.cleaned.json`

### 4. 图谱层清洗

**输入**

- `data/kg_output/entities.json`
- `data/kg_output/relations.json`
- `data/kg_output/triples.json`
- 元素层、文献层清洗产物

**目标**

- 产出更干净的实体、关系、三元组集
- 为后续是否落地 `asset_aliases/asset_relations` 或扩展图谱表提供可验证基础

**实体规则**

- `name` 去空白、清理控制字符
- 过滤 HTML 标签名、乱码残片、明显无语义短片段
- `aliases` 去空值、去重、与主名归一
- 与元素主索引做优先归一，命中则补充 `canonical_element_id`
- 保留 `type/frequency/confidence`

**关系与三元组规则**

- `subject/object/relation` 去空白、标准化大小写与分隔符
- 过滤以下情况：
  - 主语或宾语为空
  - 主语或宾语明显为残句片段
  - 关系不在允许集合且无法映射
  - 置信度低于阈值
- 关系类型归一到受控集合：
  - `related_to`
  - `regulates`
  - `activates`
  - `inhibits`
  - `binds_to`
  - `phosphorylates`
  - `produces`
  - `composed_of`
  - `located_in`
  - 其他保留到 `other`
- 对可通过实体归一命中的主宾语补 `canonical_entity`
- 对来源证据文本保留但不改写

**输出**

- `entities.cleaned.json`
- `relations.cleaned.json`
- `triples.cleaned.json`
- `alias_index.cleaned.json`

## 异常记录契约

`exceptions.jsonl` 每行一个异常对象，字段固定为：

```json
{
  "stage": "literature",
  "source_file": "data_2/data/normalized/38768776.json",
  "record_id": "38768776",
  "field": "content",
  "issue_type": "html_removed",
  "severity": "info",
  "original_value": "<h4>Aims</h4>...",
  "action": "strip_html",
  "message": "正文中检测到 HTML 标签，已转为纯文本。"
}
```

异常分类至少覆盖：

- `invalid_encoding`
- `missing_required_field`
- `invalid_doi`
- `invalid_url`
- `empty_content_after_cleaning`
- `duplicate_merged`
- `unknown_element_reference`
- `unmatched_literature_reference`
- `noise_entity_filtered`
- `broken_relation_filtered`
- `low_confidence_relation_filtered`
- `html_removed`

## 统计与验证契约

### cleaning_summary.json

必须包含：

- 输入文件数
- 各层原始记录数
- 各层保留记录数
- 各层过滤记录数
- 各类异常计数
- 去重合并数
- 元素与文献映射保留数
- 图谱实体/关系/三元组保留率

### validation_summary.json

必须包含：

- 关键输出文件是否存在
- 元素主索引唯一性检查
- 文献去重键唯一性检查
- 合法 DOI 比例
- 合法 URL 比例
- 映射层有效元素命中率
- 映射层有效文献命中率
- 图谱层空主语/空宾语残留数
- 图谱层低质量噪声残留抽样
- 是否通过整体验证

## 参数与运行方式

建议脚本入口：

```text
python scripts/clean_data_bundle.py D:\ljn-xm\data_2.zip
```

建议支持参数：

- `--output-root`
- `--min-relation-confidence`
- `--overwrite`
- `--sample-limit`

默认行为：

- 输出到项目根目录 `build/`
- 若目标目录存在，则覆盖重建本次清洗产物

## 测试策略

测试至少分三层：

1. 纯函数单测

- 文件名清洗
- DOI/URL 规范化
- HTML 转纯文本
- 元素归一键生成
- 噪声实体判定
- 关系片段过滤判定

2. 小样本集成测试

- 用最小 ZIP 样本覆盖元素、文献、映射、图谱四层
- 验证清洗结果、异常记录、摘要统计

3. 当前真实数据包验证

- 不要求测试仓库中携带完整大包
- 通过脚本在本地实际运行并输出验证摘要

## 风险与边界

- 中文文件名编码问题未必都能自动恢复；无法可靠修复时只能保留原始标识并输出安全文件名
- 图谱噪声过滤规则如果过严，可能会损失部分真实关系，因此阈值应可配置
- 本轮不解决数据库字段缺口，只为后续导入提供更干净的数据层
- 本轮不做学术事实纠错，只做结构化与质量层清洗

## 实施后的下一步

清洗完成后，再进入导入阶段时，优先导入：

- 元素层到 `entries` / `text_db.terms`
- 文献层到 `documents` / `text_db.documents`

映射层与图谱层则基于本次清洗结果决定：

- 是先作为审计产物保留
- 还是扩展当前系统数据模型后再入库
