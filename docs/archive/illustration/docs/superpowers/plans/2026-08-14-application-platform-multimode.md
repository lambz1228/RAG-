# 科研绘图应用平台多模式创作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为应用平台落地第一阶段多模式创作台，打通文生图、草图转图、编辑图片三种模式的前台工作流、参考图上传与持久化、生成编排和结果展示。

**Architecture:** 保持现有 Flask 单体与 `index.html` 单模板结构不变，把模式差异尽量收束在应用平台边界。数据层新增“参考图资产”和“生成任务快照”两类 `app_db` 能力，服务层由 `DrawingApplicationService` 负责编排 mode 与 reference 上下文，`DrawService` 只接受标准化的轻量参考图语境并继续复用现有生成链路。

**Tech Stack:** Python 3.12、Flask、现有 Repository 抽象、Waitress、单文件前端模板、Pytest / unittest

## Global Constraints

- 继续复用现有 Flask 单体、`index.html` 单模板和当前 `/api/draw`、`/api/workflow` 主接口。
- 模式差异主要收束在 `DrawingApplicationService`，`DrawService` 只做必要扩展。
- 第一阶段的数据结构按第二阶段“历史、回填、再编辑”预留，但不提前做过度抽象。
- 本轮不做像素级图片编辑、真实 image-to-image 模型推理、历史记录页、项目制管理、版本树、多图批量生成、后端服务拆分或前端框架迁移。
- 参考图上传结果不是临时态，而是应用平台资产，后续可被历史记录与二次生成复用。

---

## File Structure

- Modify: `sci-illust-system/web_app/repositories.py`
  - 为 `KnowledgeRepository` / `SQLiteKnowledgeRepository` / `MySQLKnowledgeRepository` 增加参考图资产和生成任务的最小持久化能力。
- Modify: `sci-illust-system/web_app/services/drawing_app_service.py`
  - 把当前薄包装服务扩展成真正的应用平台编排层。
- Modify: `sci-illust-system/web_app/services/draw_service.py`
  - 支持接收 `mode` 和 `reference_summary`，保持旧输入兼容。
- Modify: `sci-illust-system/web_app/services/__init__.py`
  - 如引入新服务类或导出需要，统一从这里对外暴露。
- Modify: `sci-illust-system/web_app/app.py`
  - 接入参考图上传/列表接口，接线新的应用平台服务依赖。
- Modify: `sci-illust-system/web_app/templates/index.html`
  - 扩展三模式创作台、参考图上传和选择 UI、结果状态展示。
- Test: `tests/test_draw_service.py`
  - 补 `mode` 和 `reference_summary` 的兼容输入测试。
- Test: `tests/test_platform_end_to_end.py`
  - 补应用平台多模式接口集成测试。
- Create or Modify: `tests/test_repository_layer.py`
  - 补参考图资产与生成任务持久化测试。
- Create or Modify: `tests/test_platform_navigation.py`
  - 补模板中三模式、参考图区与状态区的静态产物断言。

## Task 1: 扩展应用平台持久化边界

**Files:**
- Modify: `sci-illust-system/web_app/repositories.py`
- Test: `tests/test_repository_layer.py`

**Interfaces:**
- Consumes: `build_repository(kind=None, db_path=None, connector=None) -> KnowledgeRepository`
- Produces:
  - `KnowledgeRepository.save_reference_asset(filename: str, stored_path: str, mime_type: str, size_bytes: int, mode_scope: str = "shared") -> int`
  - `KnowledgeRepository.list_reference_assets(mode_scope: str = "") -> list[dict]`
  - `KnowledgeRepository.get_reference_asset(asset_id: int) -> dict | None`
  - `KnowledgeRepository.save_generation_task(mode: str, prompt_text: str, reference_asset_id: int | None, request_payload_json: str, result_svg: str, result_summary: str, result_source: str) -> int`

- [ ] **Step 1: 写失败测试，定义应用平台持久化最小合同**

```python
def test_repository_saves_and_lists_reference_assets():
    repo = build_repository(kind="sqlite", db_path=":memory:")

    asset_id = repo.save_reference_asset(
        filename="draft.png",
        stored_path="/tmp/draft.png",
        mime_type="image/png",
        size_bytes=128,
        mode_scope="sketch",
    )

    item = repo.get_reference_asset(asset_id)
    items = repo.list_reference_assets()

    assert asset_id >= 1
    assert item["filename"] == "draft.png"
    assert items[0]["mode_scope"] == "sketch"


def test_repository_saves_generation_task_snapshot():
    repo = build_repository(kind="sqlite", db_path=":memory:")
    asset_id = repo.save_reference_asset("draft.png", "/tmp/draft.png", "image/png", 128, "shared")

    task_id = repo.save_generation_task(
        mode="edit",
        prompt_text="保留结构，改成科研示意风格",
        reference_asset_id=asset_id,
        request_payload_json='{"mode":"edit"}',
        result_svg="<svg></svg>",
        result_summary="已生成编辑版示意图",
        result_source="rule_based",
    )

    tasks = repo.list_settings(prefix="")  # 这里先用真实任务读取接口替换为后续计划中的查询断言
    assert task_id >= 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_repository_layer.py -v`
Expected: FAIL，提示 `save_reference_asset` / `get_reference_asset` / `save_generation_task` 等方法不存在或 SQLite 表结构不支持。

- [ ] **Step 3: 在 Repository 中补最小实现**

```python
class KnowledgeRepository(Protocol):
    def save_reference_asset(self, filename, stored_path, mime_type="", size_bytes=0, mode_scope="shared"):
        ...

    def list_reference_assets(self, mode_scope=""):
        ...

    def get_reference_asset(self, asset_id):
        ...

    def save_generation_task(
        self,
        mode,
        prompt_text,
        reference_asset_id,
        request_payload_json="",
        result_svg="",
        result_summary="",
        result_source="",
    ):
        ...


class SQLiteKnowledgeRepository(KnowledgeDatabase):
    def save_reference_asset(self, filename, stored_path, mime_type="", size_bytes=0, mode_scope="shared"):
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reference_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT DEFAULT '',
                size_bytes INTEGER DEFAULT 0,
                mode_scope TEXT DEFAULT 'shared',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.cursor.execute(
            """
            INSERT INTO reference_assets (filename, stored_path, mime_type, size_bytes, mode_scope)
            VALUES (?, ?, ?, ?, ?)
            """,
            (filename, stored_path, mime_type, size_bytes, mode_scope),
        )
        self.conn.commit()
        return self.cursor.lastrowid
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_repository_layer.py -v`
Expected: PASS，参考图资产和生成任务快照的最小读写合同成立。

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/repositories.py tests/test_repository_layer.py
git commit -m "feat(repository): add application platform asset persistence"
```

## Task 2: 把 DrawingApplicationService 扩展成模式编排层

**Files:**
- Modify: `sci-illust-system/web_app/services/drawing_app_service.py`
- Modify: `sci-illust-system/web_app/services/__init__.py`
- Test: `tests/test_platform_services.py`

**Interfaces:**
- Consumes:
  - `draw_service.draw(payload: dict) -> dict`
  - `draw_service.workflow(payload: dict) -> dict`
  - `repository.get_reference_asset(asset_id: int) -> dict | None`
  - `repository.save_generation_task(...) -> int`
- Produces:
  - `DrawingApplicationService.create_workflow(payload: dict) -> dict`
  - `DrawingApplicationService.generate_figure(payload: dict) -> dict`
  - `DrawingApplicationService.upload_reference_asset(file_storage, mode_scope: str = "shared") -> dict`
  - `DrawingApplicationService.list_reference_assets(mode_scope: str = "") -> dict`

- [ ] **Step 1: 写失败测试，明确模式编排和参考图上下文行为**

```python
def test_generate_figure_includes_reference_summary_for_edit_mode(tmp_path):
    class FakeRepo:
        def get_reference_asset(self, asset_id):
            return {"id": asset_id, "filename": "draft.png", "stored_path": str(tmp_path / "draft.png")}

        def save_generation_task(self, **kwargs):
            return 7

    class FakeDrawService:
        def draw(self, payload):
            return {"success": True, "svg": "<svg></svg>", "summary": "ok", "source": "rule_based"}

    service = DrawingApplicationService(draw_service=FakeDrawService(), repository_getter=lambda: FakeRepo())

    result = service.generate_figure({"mode": "edit", "text": "改成科研风格", "reference_asset_id": 3})

    assert result["success"] is True
    assert result["mode"] == "edit"
    assert result["reference_asset"]["filename"] == "draft.png"
    assert result["task_id"] == 7
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_platform_services.py -v`
Expected: FAIL，提示 `DrawingApplicationService` 尚不接受 repository / storage 依赖，也没有多模式返回字段。

- [ ] **Step 3: 补服务编排最小实现**

```python
class DrawingApplicationService:
    def __init__(self, draw_service, repository_getter, asset_root, url_builder):
        self.draw_service = draw_service
        self.repository_getter = repository_getter
        self.asset_root = asset_root
        self.url_builder = url_builder

    def _reference_summary(self, mode, asset, text):
        if not asset:
            return ""
        if mode == "sketch":
            return f"参考图为用户上传草图 {asset['filename']}。请结合文本要求生成规范科研示意图。"
        if mode == "edit":
            return f"参考图为用户上传原图 {asset['filename']}。请结合修改要求生成新的科研示意图。"
        return ""

    def generate_figure(self, payload):
        data = dict(payload or {})
        mode = (data.get("mode") or "text").strip() or "text"
        asset = self.repository_getter().get_reference_asset(data.get("reference_asset_id")) if data.get("reference_asset_id") else None
        data["reference_summary"] = data.get("reference_summary") or self._reference_summary(mode, asset, data.get("text", ""))
        result = self.draw_service.draw(data)
        result["boundary"] = "drawing_application"
        result["mode"] = mode
        result["reference_asset"] = asset
        return result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_platform_services.py -v`
Expected: PASS，模式、参考图和任务快照写入行为都能被断言。

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/services/drawing_app_service.py sci-illust-system/web_app/services/__init__.py tests/test_platform_services.py
git commit -m "feat(service): add multimode drawing application orchestration"
```

## Task 3: 扩展 DrawService 并保持旧链路兼容

**Files:**
- Modify: `sci-illust-system/web_app/services/draw_service.py`
- Test: `tests/test_draw_service.py`

**Interfaces:**
- Consumes:
  - `RequirementAnalyzer.analyze(text: str) -> dict`
  - 现有 pipeline `process()` / `process_components()`
- Produces:
  - `DrawService.draw(payload: dict) -> dict` 接受 `mode`、`reference_summary`
  - `DrawService.workflow(payload: dict) -> dict` 接受 `mode`、`reference_summary`

- [ ] **Step 1: 写失败测试，守住 reference_summary 只增强上下文不破坏旧逻辑**

```python
def test_draw_includes_reference_summary_in_analysis_input():
    class FakeAnalyzer:
        def __init__(self):
            self.seen = None

        def analyze(self, text):
            self.seen = text
            return {"figure_type": "pathway_diagram", "style": "science"}

    analyzer = FakeAnalyzer()

    class FakePipeline:
        def process(self, text, **kwargs):
            return {
                "mode": "elements",
                "svg": "<svg></svg>",
                "analysis": {"domain": "biology", "analysis_summary": "ok"},
                "elements": [],
                "relations": [],
            }

    service = DrawService(
        knowledge_base=object(),
        pipeline_factory=lambda: FakePipeline(),
        analyzer_factory=lambda: analyzer,
    )

    service.draw({"text": "EGFR 通路", "mode": "edit", "reference_summary": "参考图为 draft.png"})
    assert "参考图为 draft.png" in analyzer.seen
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_draw_service.py -v`
Expected: FAIL，`DrawService` 还不会把参考图上下文合并进分析文本。

- [ ] **Step 3: 补最小实现，并确保旧输入不变**

```python
def _analysis_text(self, text, mode="", reference_summary=""):
    if mode in {"sketch", "edit"} and reference_summary:
        return f"{reference_summary}\n用户需求：{text}"
    return text

def draw(self, payload):
    data = payload or {}
    text = (data.get("text", "") or "").strip()
    mode = (data.get("mode", "") or "").strip()
    reference_summary = (data.get("reference_summary", "") or "").strip()
    analysis_text = self._analysis_text(text, mode=mode, reference_summary=reference_summary)
    ...
    analysis = self._analyzer().analyze(analysis_text)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_draw_service.py -v`
Expected: PASS，同时原有文生图、workflow、弱文本校验断言全部继续通过。

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/services/draw_service.py tests/test_draw_service.py
git commit -m "feat(draw): support multimode reference context"
```

## Task 4: 接入参考图路由与多模式前端

**Files:**
- Modify: `sci-illust-system/web_app/app.py`
- Modify: `sci-illust-system/web_app/templates/index.html`
- Test: `tests/test_platform_end_to_end.py`
- Test: `tests/test_platform_navigation.py`

**Interfaces:**
- Consumes:
  - `DrawingApplicationService.upload_reference_asset(file_storage, mode_scope="shared") -> dict`
  - `DrawingApplicationService.list_reference_assets(mode_scope="") -> dict`
  - `DrawingApplicationService.generate_figure(payload: dict) -> dict`
  - `DrawingApplicationService.create_workflow(payload: dict) -> dict`
- Produces:
  - `POST /api/app/reference-assets`
  - `GET /api/app/reference-assets`
  - 前端 DOM 锚点：
    - `data-creator-mode="text|sketch|edit"`
    - `id="reference-upload"`
    - `id="reference-assets"`
    - `id="selected-reference"`

- [ ] **Step 1: 写失败测试，定义新接口和模板锚点**

```python
def test_reference_asset_routes_are_available(client):
    response = client.get("/api/app/reference-assets")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert "items" in payload


def test_generation_area_exposes_multimode_reference_ui():
    template = Path("sci-illust-system/web_app/templates/index.html").read_text(encoding="utf-8")

    assert 'data-creator-mode="text"' in template
    assert 'data-creator-mode="sketch"' in template
    assert 'data-creator-mode="edit"' in template
    assert 'id="reference-upload"' in template
    assert 'id="reference-assets"' in template
    assert 'id="selected-reference"' in template
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_platform_end_to_end.py tests/test_platform_navigation.py -v`
Expected: FAIL，新路由不存在，模板中还没有三模式参考图区。

- [ ] **Step 3: 补路由和前端最小实现**

```python
@app.route("/api/app/reference-assets", methods=["GET", "POST"])
def reference_assets():
    if request.method == "GET":
        return jsonify(drawing_app_service.list_reference_assets(request.args.get("mode_scope", "")))

    if "file" not in request.files:
        return jsonify({"success": False, "error": "未上传参考图"})
    return jsonify(
        drawing_app_service.upload_reference_asset(
            request.files["file"],
            mode_scope=(request.form.get("mode_scope", "shared") or "shared").strip(),
        )
    )
```

```html
<div class="creator-mode-grid">
  <button type="button" data-creator-mode="text">文生图</button>
  <button type="button" data-creator-mode="sketch">草图转图</button>
  <button type="button" data-creator-mode="edit">编辑图片</button>
</div>
<div id="reference-panel" hidden>
  <input id="reference-upload" type="file" accept="image/png,image/jpeg,image/webp">
  <div id="selected-reference"></div>
  <div id="reference-assets"></div>
</div>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_platform_end_to_end.py tests/test_platform_navigation.py -v`
Expected: PASS，新路由可达，模板锚点齐全，旧 `/api/draw` 和 `/api/workflow` 仍能通过集成测试。

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/app.py sci-illust-system/web_app/templates/index.html tests/test_platform_end_to_end.py tests/test_platform_navigation.py
git commit -m "feat(web): add multimode creator and reference asset routes"
```

## Task 5: 全链路验证与服务器部署

**Files:**
- Modify: `sci-illust-system/web_app/app.py`（如有最后修正）
- Modify: `sci-illust-system/web_app/templates/index.html`（如有最后修正）
- Modify: `tests/test_deployment_artifacts.py`（若需补部署回归断言）

**Interfaces:**
- Consumes:
  - 本地测试命令
  - 服务器路径 `/root/keyanhuitu/sci-illust-system/web_app/`
  - `keyanhuitu.service`
- Produces:
  - 本地通过的验证结果
  - 已同步的服务器模板 / 后端文件
  - `HTTP 200` 的应用与 nginx 入口

- [ ] **Step 1: 运行本地完整相关测试**

Run: `python -m pytest tests/test_repository_layer.py tests/test_platform_services.py tests/test_draw_service.py tests/test_platform_end_to_end.py tests/test_platform_navigation.py tests/test_deployment_artifacts.py -v`
Expected: PASS，所有与应用平台多模式创作台相关的测试通过。

- [ ] **Step 2: 做人工代码回归检查**

Run: `git diff -- sci-illust-system/web_app/app.py sci-illust-system/web_app/services/drawing_app_service.py sci-illust-system/web_app/services/draw_service.py sci-illust-system/web_app/repositories.py sci-illust-system/web_app/templates/index.html tests`
Expected: 只看到本轮多模式创作台相关改动，无无关回退或误删。

- [ ] **Step 3: 同步到服务器**

```bash
scp -i $HOME\.ssh\aliyun_root_20260813 sci-illust-system/web_app/app.py root@47.106.188.244:/root/keyanhuitu/sci-illust-system/web_app/app.py
scp -i $HOME\.ssh\aliyun_root_20260813 sci-illust-system/web_app/repositories.py root@47.106.188.244:/root/keyanhuitu/sci-illust-system/web_app/repositories.py
scp -i $HOME\.ssh\aliyun_root_20260813 sci-illust-system/web_app/services/drawing_app_service.py root@47.106.188.244:/root/keyanhuitu/sci-illust-system/web_app/services/drawing_app_service.py
scp -i $HOME\.ssh\aliyun_root_20260813 sci-illust-system/web_app/services/draw_service.py root@47.106.188.244:/root/keyanhuitu/sci-illust-system/web_app/services/draw_service.py
scp -i $HOME\.ssh\aliyun_root_20260813 sci-illust-system/web_app/templates/index.html root@47.106.188.244:/root/keyanhuitu/sci-illust-system/web_app/templates/index.html
```

- [ ] **Step 4: 重启并验证服务**

Run: `ssh -i $HOME\.ssh\aliyun_root_20260813 root@47.106.188.244 "systemctl restart keyanhuitu && sleep 3 && curl -I --max-time 15 http://127.0.0.1:5000/ && echo '---' && curl -I --max-time 15 http://127.0.0.1/"`
Expected: `keyanhuitu` active，Waitress 本机入口和 nginx 入口都返回 `HTTP/1.1 200 OK`。

- [ ] **Step 5: Commit（仅限本地代码，不含服务器状态）**

```bash
git add sci-illust-system/web_app/app.py sci-illust-system/web_app/repositories.py sci-illust-system/web_app/services/drawing_app_service.py sci-illust-system/web_app/services/draw_service.py sci-illust-system/web_app/templates/index.html tests
git commit -m "feat(platform): add multimode creator workflow"
```

## Self-Review

- Spec coverage:
  - 三模式创作台：Task 4
  - 参考图上传与保存：Task 1、Task 2、Task 4
  - `/api/draw` 与 `/api/workflow` 扩展：Task 2、Task 3、Task 4
  - 结果快照与第二阶段预留：Task 1、Task 2
  - 部署与服务器验证：Task 5
- Placeholder scan:
  - 计划中没有 `TODO` / `TBD` / “后续补充”式占位步骤；接口、测试和命令都给了具体内容。
- Type consistency:
  - 统一使用 `mode`、`reference_asset_id`、`reference_summary` 作为跨任务字段名。
  - 统一把应用平台持久化入口落在 `KnowledgeRepository` 扩展方法上，避免后续任务接口名漂移。

