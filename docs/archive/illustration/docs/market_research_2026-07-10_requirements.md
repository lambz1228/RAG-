# 科研绘图全网调研报告（对照 2026-07-10 需求）

> 调研日期：2026-08-20  
> 对照需求：《科研绘图》五项工作内容（2026-07-10）  
> 本系统：Keyanhuitu / Sci-Illust（公开地址 http://47.106.188.244/）

---

## 1. 结论摘要

当前行业共识是：**AI 只负责出初稿，交付必须是“可编辑矢量画布”**（拖动、改标签、改连线、区域重绘）。老师反馈的「生图后不可编辑、组件不可拖动」正对应这一行业分水岭。

本系统已具备「需求 → 结构化流程 → 元素/关系 → SVG 构图」的主链路；本次已补上**生成后组件拖动 + 连线跟随 + 下载保留新位置**。相对头部产品，差距主要在：线稿着色深度、元素库规模、画布级编辑（缩放/对齐/属性面板）、以及「在成图中定位元素」的交互闭环。

---

## 2. 需求逐条对照

### （1）线稿图着色

| 代表产品 / 方案 | 能力要点 | 与本系统关系 |
|---|---|---|
| [LabFig Sketch-to-Figure](https://labfig.com/sketch-to-figure) | 白板/手绘照片 → 规整几何、识别箭头、可编辑矢量 | 对标最直接 |
| [SciDraw](https://sci-draw.com/) Sketch/Image Edit | 草图/参考图 → 专业图，SVG/PPTX 导出 | 商业 SaaS |
| [FigEditor](https://figeditor.ai/) | 草图 + 区域修订 + 矢量化 | 强调局部重绘 |
| [Fig0](https://fig0.ai/) / [FigCanvas](https://figcanvas.com/) | Sketch-to-Figure + 无限画布 | 输入多模态 |
| BioRender AI（beta） | 生信标准库 + AI 辅助 | 金标准但贵、闭源 |
| Inkscape + [Bioicons](https://bioicons.com/) | 手工着色/换色，开源可复用 | 可作为素材后端 |

**行业做法：** 不是简单“上色滤镜”，而是 **结构重建**（框、箭头、标签）后再着色。

**本系统现状：** UI 已有「线稿/草图」模式与上传入口；需继续把草图解析接到布局/渲染，而不仅是提示词附带。

**建议优先级：** P1 — 草图 → 节点/边粗解析 → 现有 SVG 渲染器；颜色方案由风格面板控制。

---

### （2）客户绘图需求 → AI 绘图流程

| 代表产品 / 方案 | 能力要点 | 与本系统关系 |
|---|---|---|
| PaperBanana / [PaperVizAgent](https://github.com/google-research/papervizagent) | Retriever→Planner→Stylist→Visualizer→Critic 多智能体 | 学术流水线对标 |
| FigEditor Flowchart Agent | 文本 → 流程图 SVG | 偏流程类 |
| BioRender flowchart AI | 机制图/流程模板 + AI | 领域强 |
| AutoFigure（ICLR 2026） | 长文方法 → 符号布局蓝图再渲染 | 「先结构后美观」 |

**行业做法：** 先产出**可机读流程**（元素、关系、布局意图、质检），再渲染；禁止一步出图后无法追溯。

**本系统现状：** 已有「生成流程」与流程预览（元素/关系/构图/质检），与需求（2）对齐较好。

**建议优先级：** P0 维持 — 强化流程 schema 稳定性与领域模板（生/化/环/材）。

---

### （3）按流程给出元素名称，并绘制元素图形

| 代表产品 / 方案 | 能力要点 | 与本系统关系 |
|---|---|---|
| BioRender | 数万专业图标，可组合 | 库规模碾压 |
| [Bioicons](https://github.com/duerrsimon/bioicons) + diagrams.net | 开源 SVG 图标，可嵌入 draw.io | **最可落地的素材源** |
| SciDraw.io 素材库 | 社区高质量矢量插画 | 偏整图素材 |
| GPAI / SciFig / Fig0 | 生成式图标 + 画布编辑 | 生成补库缺口 |

**行业做法：** **库检索优先，生成补缺**；元素必须有稳定名称 ID，便于后续定位与替换。

**本系统现状：** 已有元素名 + 图元绘制；文案侧提到 Bioicons 匹配。库覆盖与命名规范化仍是瓶颈。

**建议优先级：** P1 — 接入 Bioicons 索引；未知元素走简化几何/生成占位并挂 `data-element`。

---

### （4）按流程 + 元素构建空间关系、大小、视角的科研配图

| 代表产品 / 方案 | 能力要点 | 与本系统关系 |
|---|---|---|
| BioRender / SciFig Vector Canvas / LabFig Canvas | 拖动、对齐、图层、导出 SVG/PPTX/PDF | 编辑体验标杆 |
| FigCanvas 无限画布 | 多图拼接、插画+数据图 | 组图场景 |
| Ai4Scholar 等 tldraw 系 | 通用无限画布 + AI | 工程可借鉴 |
| 本系统 SceneRenderer | cascade/布局器 + 正交连线 + 风格方案 | **已有构图引擎** |

**行业做法：** 布局引擎出初稿 → **人在画布上微调**（拖动是最低门槛）。

**本系统现状（本次已修）：**

- 节点：`scene-node` + `transform` + `data-x/y/w/h`
- 连线：`scene-edge` + `data-source/target`，拖动时路径重算
- 下载前同步预览中的编辑结果

**建议优先级：** P1 — 对齐/吸附、多选、属性面板（字号/颜色）、缩放画布；P2 — PPTX 可编辑对象导出。

---

### （5）按需求在已完成配图中找出对应元素图形

| 代表产品 / 方案 | 能力要点 | 与本系统关系 |
|---|---|---|
| [AutoFigure-Edit](https://github.com/ResearAI/AutoFigure-Edit)（ICLR 2026） | LLM 出图 → SAM3 分割 → 结构化 SVG → 浏览器编辑 | **学术前沿，直接对标（5）** |
| FigEditor / LabFig 区域编辑 | 框选区域提示词重绘 | 交互友好 |
| 结构化 SVG（本系统路线） | 生成时即带 `id` / `data-element` | **更稳、更省算力** |

**行业两条路：**

1. **生成时结构化**（推荐）：每个组件自带语义 ID，侧栏点元素名 → 高亮/选中。  
2. **事后分割**：对位图用 SAM 等检出区域再转 SVG（AutoFigure-Edit）。

**本系统现状：** SVG 已有 `id=el_*`、`data-element=...`；缺「需求词 → 高亮定位」的前端交互。

**建议优先级：** P0/P1 — 元素标签点击高亮对应 `scene-node`；支持按客户原文实体名模糊匹配。

---

## 3. 竞品速览（按定位）

| 类型 | 产品 | 编辑性 | 领域深度 | 开源/可自建 |
|---|---|---|---|---|
| 金标准库+画布 | BioRender | 强 | 极强（生信） | 否 |
| AI 生成+矢量画布 | LabFig / SciFig / Fig0 / FigCanvas / FigEditor | 强 | 中–强 | 否 |
| 多智能体出图 | PaperBanana / PaperVizAgent | 弱（多为栅格） | 论文方法图 | 代码开源 |
| 可编辑科研插画研究 | AutoFigure-Edit | 强（SVG+编辑器） | 方法示意图 | 开源 |
| 开源素材+手工 | Bioicons + Inkscape / draw.io | 强 | 依赖用户 | 是 |
| 本系统 Sci-Illust | 流程+SVG+**现已支持拖动** | 中（起步） | 生/化/环/材可扩展 | 自建可控 |

---

## 4. 与老师反馈的直接关系

| 反馈 | 行业要求 | 本系统动作 |
|---|---|---|
| 生图后不可编辑 | 矢量组件可交互 | 预览区启用 SVG 编辑层 |
| 组件不可拖动 | 节点可拖、边跟随 | `scene-node` 拖动 + `scene-edge` 更新 |
| （隐含）交付可用 | 下载保留编辑结果 | 下载前 `syncLastSvgFromPreview` |

线上验证（2026-08-20）：多节点 SVG 含 `scene-node`/`scene-edge`，且 `data-source/target` 与节点 `id` 一致。

---

## 5. 建议产品路线图（对齐五项需求）

1. **已完成：** 生成后拖动编辑（需求 4 的最低可用编辑）。  
2. **下一迭代（1–2 周）：**  
   - 侧栏元素点击 → 图中高亮定位（需求 5）  
   - 线稿上传真正驱动布局，而不只是附带说明（需求 1）  
3. **中期：** Bioicons 检索入库 + 未知元素占位策略（需求 3）  
4. **中期：** 流程模板按生/化/环/材分域 + Critic 质检轮（需求 2）  
5. **远期：** 对齐/图层/属性面板、PPTX 对象导出；可选接入 SAM 类「位图→可编辑」兜底（需求 4+5）

---

## 6. 主要参考链接

- LabFig: https://labfig.com/  
- SciFig Vector Canvas: https://scifig.ai/app/vector-canvas  
- FigCanvas: https://figcanvas.com/  
- FigEditor: https://figeditor.ai/  
- Fig0: https://fig0.ai/  
- SciDraw: https://sci-draw.com/  
- Bioicons: https://bioicons.com/ / https://github.com/duerrsimon/bioicons  
- PaperBanana / PaperVizAgent: https://github.com/google-research/papervizagent  
- AutoFigure-Edit: https://github.com/ResearAI/AutoFigure-Edit （ICLR 2026）  
- arXiv AutoFigure-Edit: https://arxiv.org/abs/2603.06674  

---

## 7. 一句话定位建议（对外可讲）

> 我们不做一次性 AI 出图，而是「客户需求 → 可校验的 AI 绘图流程 → 带语义的矢量元素构图 → 可拖动精修」；下一步补齐线稿驱动与成图内元素定位，对齐国际产品的 Generate-then-Edit 主路径。
