# 本机DeepSeek运行与自动评价

本轮在Windows本机重建语料和真实E5索引，使用DeepSeek Flash执行v8提示的开发回归。模型与输出协议发生变化，本轮不复用Qwen历史成绩，也不是历史Qwen配置的精确复现。

## 环境与配置

虚拟环境为项目根目录的`.venv`。`.env`引用系统变量`ds_key`，不保存密钥值；可分发的配置见`config/deepseek-semantic.env.example`。运行数据位于`data/local-20261116/`，原始响应、首次失败与重试分别保留。模型文件在`data/models/multilingual-e5-small/`，文件摘要由下载器核对。

```powershell
.venv\Scripts\python -X utf8 app.py
```

也可使用`start.bat`，其会优先选择项目虚拟环境。Windows运行研究脚本时显式使用`-X utf8`，避免依赖默认GBK编码。

## 固定的实验边界

- 保留原有开发题草稿，构建时核对全文摘要及原文锚点；不按本轮成绩修改题目或参考答案。
- 生成使用`deepseek-flash`，`evidence-v8-direct-information`提示版本，`answer`输出模式与`json_object`响应格式，最多4096输出词元、6000字符证据预算。历史Qwen运行采用过`claims/json_schema`，配置差异登记在本轮协议。
- 当前生成适配器未显式传入DeepSeek思考开关，按官方默认开启思考模式、high强度；思考模式下temperature参数不生效，不宣称确定性复现。官方接口别名后续可能升级，运行日期和配置摘要需共同报告。
- 12题应用回归和三组36份对照独立保存；不将两套结果合并为48个独立问题。每组仅10道有出处题和2道指定DOI缺失题。
- E5编码、查询术语补充与旧开发配置一致；不将哈希向量成绩称为语义检索。

## 模型自动评价

`scripts/evaluate_answers_auto.py`使用不同的`deepseek-v4-pro`作为裁判。每份答卷隐藏方案名称，以固定随机顺序评价，提供相同参考要点、参考原文、指定来源缺失依据及该答卷实际取得的证据。参考答案只进入裁判，不发送给被测生成模型。

正确性与完整性分别取0、1、2；遗漏和无支持事实分别判断。引用支持依据实际引用片段评价，未使用片段不可代替引用支持。无支持断言指标以回答为单位，不是逐论断幻觉率。裁判显式关闭思考模式，使用JSON输出、temperature=0和4096输出词元上限；原始响应与匿名评价输入分别保存。

程序失败与裁判解析失败单列，比例分母为成功裁判数。拒答与生成回退单独统计，不把摘录回退称为模型成功。人工字段保持为空。

```powershell
.venv\Scripts\python -X utf8 scripts/evaluate_answers_auto.py --run output/local-deepseek-v8/deployment --dataset output/local-deepseek-v8/answers.json --output output/local-deepseek-v8/judge-deployment
.venv\Scripts\python -X utf8 scripts/evaluate_answers_auto.py --run output/local-deepseek-v8/taskbook --dataset output/local-deepseek-v8/answers.json --output output/local-deepseek-v8/judge-taskbook
```

裁判与生成模型同属DeepSeek系列，可能存在相关偏差；一次模型裁判也可能误判。这是开发集上的模型自动评价，不是人工、专家确认或独立测试。指定DOI缺失题不能代表一般领域无答案能力。

官方依据：[模型标识](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[JSON输出](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)、[思考模式及参数行为](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。

实际数据量、回归和评价结果见[本轮结果报告](本机DeepSeek回归与自动评价结果.md)。
