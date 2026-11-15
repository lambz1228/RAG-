# Workbench Shell Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将科研绘图工作台改造成浅色工作台壳层，完成左侧导航、顶部栏和应用平台主框架的高仿重排，同时保持现有接口和主要交互行为可用。

**Architecture:** 继续复用 `sci-illust-system/web_app/templates/index.html` 这一单模板入口，在不拆后端接口和页面切换机制的前提下重构页面壳层与应用平台 DOM 结构。实现以增量 CSS 变量替换、局部类名收口和最小 JavaScript 调整为主，并用前端产物测试守住导航、关键容器和交互锚点。

**Tech Stack:** Flask 模板、原生 HTML/CSS/JavaScript、Python unittest、pytest

## Global Constraints

- 只改左侧导航、顶部栏和应用平台主框架，其他业务页不重做内部信息架构。
- 不重构后端接口，不更换现有数据流，不新增真实图片编辑、草图转图等完整后端能力。
- 复用现有模板、页面切换机制和接口调用逻辑。
- 优先通过 HTML 结构调整和 CSS 分层完成改版。
- JavaScript 仅做最小必要改动，保证现有按钮、状态文案、结果渲染继续可用。
- 修改集中在 `sci-illust-system/web_app/templates/index.html`，测试补到前端产物级别。
- 左侧导航保留 `data-page` 驱动的页面切换。
- 顶栏保留现有状态胶囊数据来源，本轮不接新接口。
- 保持接口、按钮 ID、关键容器 ID 不变，确保 JS 绑定不断裂。
- 主按钮不使用真正 `fixed`，避免移动端遮挡。

---

## File Structure

- `sci-illust-system/web_app/templates/index.html`
  - 单文件模板，包含全站壳层样式、应用平台 DOM 结构和前端交互脚本；本次主要改造目标。
- `tests/test_deployment_artifacts.py`
  - 产物级前端断言，适合补壳层类名、关键文案、结构存在性检查。
- `tests/test_platform_navigation.py`
  - 导航和应用平台命名相关断言，适合补主框架和关键按钮语义回归。

### Task 1: 锁定新的壳层结构断言

**Files:**
- Modify: `tests/test_deployment_artifacts.py`
- Modify: `tests/test_platform_navigation.py`
- Test: `tests/test_deployment_artifacts.py`
- Test: `tests/test_platform_navigation.py`

**Interfaces:**
- Consumes: `read_text(relative_path) -> str`
- Produces: 针对 `index.html` 的结构断言，包括 `workbench-shell`、`workbench-topbar`、`creator-sidebar`、`creator-canvas`、浅色导航文案和主按钮文案

- [ ] **Step 1: Write the failing tests**

```python
def test_web_ui_uses_light_workbench_shell(self):
    content = read_text("sci-illust-system/web_app/templates/index.html")

    self.assertIn('class="app workbench-shell"', content)
    self.assertIn('class="topbar workbench-topbar"', content)
    self.assertIn('class="draw-layout creator-workbench"', content)
    self.assertIn('class="card draw-panel creator-sidebar"', content)
    self.assertIn('class="card preview creator-canvas"', content)
    self.assertIn("生成模式", content)
    self.assertIn("图片描述", content)
    self.assertIn("图片分辨率", content)
    self.assertIn("图片生成数量", content)

def test_generation_area_uses_creator_copy(self):
    self.assertIn(">应用平台<", self.template)
    self.assertIn("准备好开始创作了吗？", self.template)
    self.assertIn("生成图片", self.template)
    self.assertIn("多轮编辑", self.template)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_deployment_artifacts.py tests/test_platform_navigation.py -k "workbench_shell or creator_copy" -v`  
Expected: FAIL，提示新的壳层类名或新文案尚未出现在 `index.html`

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_deployment_artifacts.py tests/test_platform_navigation.py
git commit -m "test(web): capture workbench shell redesign targets"
```

### Task 2: 重构左侧导航与顶部栏壳层

**Files:**
- Modify: `sci-illust-system/web_app/templates/index.html`
- Test: `tests/test_deployment_artifacts.py`
- Test: `tests/test_platform_navigation.py`

**Interfaces:**
- Consumes: 现有导航按钮 `button.nav-item[data-page]`、现有状态区域 `.status-pill`
- Produces: `class="app workbench-shell"`、`class="sidebar workbench-sidebar"`、`class="topbar workbench-topbar"`，以及浅色壳层 CSS 变量

- [ ] **Step 1: Write the failing test for shell classes**

```python
def test_web_ui_uses_light_workbench_shell(self):
    content = read_text("sci-illust-system/web_app/templates/index.html")

    self.assertIn('class="app workbench-shell"', content)
    self.assertIn('class="sidebar workbench-sidebar"', content)
    self.assertIn('class="topbar workbench-topbar"', content)
    self.assertIn("--surface-page: #f6f8f4;", content)
    self.assertIn("--accent-soft: #e4f4e8;", content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deployment_artifacts.py::DeploymentArtifactsTest::test_web_ui_uses_light_workbench_shell -v`  
Expected: FAIL，提示 `workbench-shell` 或新的浅色变量不存在

- [ ] **Step 3: Write minimal implementation**

```html
<div class="app workbench-shell">
  <aside class="sidebar workbench-sidebar">
    ...
  </aside>
  <main class="main">
    <div class="workspace">
      <div class="topbar workbench-topbar">
```

```css
:root {
  --surface-page: #f6f8f4;
  --surface-panel: #ffffff;
  --surface-muted: #f2f5ef;
  --border-soft: #d8e3d4;
  --text-strong: #1e2a1f;
  --text-muted-soft: #68756a;
  --accent: #56b36d;
  --accent-soft: #e4f4e8;
}
body {
  color: var(--text-strong);
  background: var(--surface-page);
}
.workbench-shell {
  grid-template-columns: 264px 1fr;
  gap: 0;
}
.workbench-sidebar {
  position: sticky;
  top: 0;
  width: 100%;
  min-height: 100vh;
  padding: 0;
  border: 0;
  border-right: 1px solid var(--border-soft);
  border-radius: 0;
  background: var(--surface-panel);
  box-shadow: none;
}
.workbench-topbar {
  align-items: center;
  min-height: 56px;
  padding: 0 8px 18px;
  border-bottom: 1px solid var(--border-soft);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_deployment_artifacts.py tests/test_platform_navigation.py -k "workbench_shell" -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/templates/index.html tests/test_deployment_artifacts.py tests/test_platform_navigation.py
git commit -m "feat(web): rebuild workbench shell frame"
```

### Task 3: 重构应用平台左侧参数面板

**Files:**
- Modify: `sci-illust-system/web_app/templates/index.html`
- Test: `tests/test_deployment_artifacts.py`
- Test: `tests/test_platform_navigation.py`

**Interfaces:**
- Consumes: `draw-input`、`draw-layout-select`、`draw-style-select`、`draw-size-select`、`draw-model-select`、`draw-submit`、`workflow-submit`
- Produces: `creator-workbench`、`creator-sidebar`、`creator-option-group`、`creator-mode-grid` 等容器，且保留原有 DOM ID

- [ ] **Step 1: Write the failing test for creator sidebar structure**

```python
def test_web_ui_exposes_creator_sidebar_sections(self):
    content = read_text("sci-illust-system/web_app/templates/index.html")

    self.assertIn('class="draw-layout creator-workbench"', content)
    self.assertIn('class="card draw-panel creator-sidebar"', content)
    self.assertIn("生成模式", content)
    self.assertIn("图片描述", content)
    self.assertIn("图片文字语言", content)
    self.assertIn("图片分辨率", content)
    self.assertIn("图片生成数量", content)
    self.assertIn("高级选项", content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deployment_artifacts.py::DeploymentArtifactsTest::test_web_ui_exposes_creator_sidebar_sections -v`  
Expected: FAIL，提示新结构或新分组标题不存在

- [ ] **Step 3: Write minimal implementation**

```html
<section class="page" id="page-drawingApp">
  <div class="draw-layout creator-workbench">
    <div class="card draw-panel creator-sidebar">
      <div class="creator-option-group">
        <div class="section-title"><h3>生成模式</h3></div>
        <div class="creator-mode-grid">...</div>
      </div>
      <div class="creator-option-group">
        <label class="form-label" for="draw-input">图片描述</label>
        <textarea class="field" id="draw-input"></textarea>
      </div>
      <div class="creator-option-group">
        <label class="form-label" for="draw-language-select">图片文字语言</label>
        <select class="field" id="draw-language-select">
          <option value="zh-CN">简体中文（默认）</option>
        </select>
      </div>
```

```css
.creator-workbench {
  grid-template-columns: 380px minmax(0, 1fr);
  gap: 0;
  min-height: calc(100vh - 120px);
  border: 1px solid var(--border-soft);
  border-radius: 18px;
  overflow: hidden;
  background: var(--surface-panel);
}
.creator-sidebar {
  gap: 16px;
  padding: 18px;
  border: 0;
  border-right: 1px solid var(--border-soft);
  border-radius: 0;
  background: #fbfcf9;
  box-shadow: none;
}
.creator-option-group {
  padding-bottom: 16px;
  border-bottom: 1px solid #e8eee5;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_deployment_artifacts.py tests/test_platform_navigation.py -k "creator_sidebar or generation_area" -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/templates/index.html tests/test_deployment_artifacts.py tests/test_platform_navigation.py
git commit -m "feat(web): reshape creator sidebar controls"
```

### Task 4: 重构应用平台右侧空状态与预览工作区

**Files:**
- Modify: `sci-illust-system/web_app/templates/index.html`
- Test: `tests/test_deployment_artifacts.py`
- Test: `tests/test_platform_navigation.py`

**Interfaces:**
- Consumes: `svg-preview`、`download-svg`、`draw-summary`、`draw-elements`、`draw-relations`、`workflow-preview-body`
- Produces: `creator-canvas`、`creator-empty-state`、浅色预览卡和保留原有渲染目标 ID

- [ ] **Step 1: Write the failing test for creator canvas copy**

```python
def test_generation_area_uses_creator_copy(self):
    self.assertIn("准备好开始创作了吗？", self.template)
    self.assertIn("AI 生成", self.template)
    self.assertIn("多轮编辑", self.template)
    self.assertIn("风格多样", self.template)
    self.assertIn('class="card preview creator-canvas"', self.template)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_platform_navigation.py::PlatformNavigationTest::test_generation_area_uses_creator_copy -v`  
Expected: FAIL，提示新空状态文案或 `creator-canvas` 不存在

- [ ] **Step 3: Write minimal implementation**

```html
<div class="card preview creator-canvas">
  <div class="preview-head creator-canvas-head">
    <div>
      <h3>生成预览</h3>
      <p id="draw-summary">等待输入绘图需求</p>
    </div>
    <button class="btn" id="download-svg" type="button" disabled>下载 SVG</button>
  </div>
  <div class="preview-body" id="svg-preview">
    <div class="creator-empty-state">
      <div class="creator-empty-icon">✦</div>
      <strong>准备好开始创作了吗？</strong>
      <span>选择模板、风格和宽高比，输入您的创意描述，即可生成专业的科研插图。</span>
    </div>
  </div>
```

```css
.creator-canvas {
  border: 0;
  border-radius: 0;
  box-shadow: none;
  background: var(--surface-panel);
}
.creator-empty-state {
  display: grid;
  place-items: center;
  gap: 14px;
  min-height: 460px;
  padding: 48px 32px;
  text-align: center;
}
.creator-empty-icon {
  width: 76px;
  height: 76px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: linear-gradient(135deg, #a34cf4, #ff4ca1);
  color: #fff;
  font-size: 30px;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_platform_navigation.py tests/test_deployment_artifacts.py -k "creator_copy or creator_canvas" -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/templates/index.html tests/test_deployment_artifacts.py tests/test_platform_navigation.py
git commit -m "feat(web): restyle creator canvas workspace"
```

### Task 5: 补齐交互态并做回归验证

**Files:**
- Modify: `sci-illust-system/web_app/templates/index.html`
- Modify: `tests/test_deployment_artifacts.py`
- Test: `tests/test_deployment_artifacts.py`
- Test: `tests/test_platform_navigation.py`

**Interfaces:**
- Consumes: `switchPage(name)`、`generateDrawing()`、`generateWorkflow()`、`downloadSvg()`
- Produces: 模式按钮选中态、高级选项折叠样式、最终壳层断言和回归通过记录

- [ ] **Step 1: Write the failing test for preserved behavior anchors**

```python
def test_creator_shell_preserves_generation_hooks(self):
    content = read_text("sci-illust-system/web_app/templates/index.html")

    self.assertIn('id="draw-submit"', content)
    self.assertIn('id="workflow-submit"', content)
    self.assertIn('id="download-svg"', content)
    self.assertIn('function generateDrawing()', content)
    self.assertIn('function generateWorkflow()', content)
    self.assertIn('function switchPage(name)', content)
```

- [ ] **Step 2: Run test to verify it fails if anchors were disturbed**

Run: `python -m pytest tests/test_deployment_artifacts.py::DeploymentArtifactsTest::test_creator_shell_preserves_generation_hooks -v`  
Expected: 若前几步改动破坏锚点则 FAIL；若仍 PASS，则记录为守护测试并继续下一步

- [ ] **Step 3: Write minimal implementation**

```css
.creator-mode-button.is-active {
  color: #215c2e;
  background: var(--accent-soft);
  border-color: #8dd09a;
}
.creator-advanced[open] .creator-advanced-body {
  display: grid;
}
```

```javascript
document.querySelectorAll("[data-creator-mode]").forEach(function(item) {
  item.addEventListener("click", function() {
    document.querySelectorAll("[data-creator-mode]").forEach(function(node) {
      node.classList.remove("is-active");
    });
    item.classList.add("is-active");
  });
});
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_deployment_artifacts.py tests/test_platform_navigation.py -v`  
Expected: PASS

Run: `python -m unittest discover -s tests -p "test_*.py"`  
Expected: 现有相关测试保持通过；若全量过慢，至少记录并运行 `test_deployment_artifacts.py`、`test_platform_navigation.py`

- [ ] **Step 5: Commit**

```bash
git add sci-illust-system/web_app/templates/index.html tests/test_deployment_artifacts.py tests/test_platform_navigation.py
git commit -m "test(web): verify workbench shell behavior anchors"
```

### Task 6: 部署到服务器并验证入口

**Files:**
- Modify: `sci-illust-system/web_app/templates/index.html`
- Test: 服务器运行中的 `/` 入口

**Interfaces:**
- Consumes: `aliyun_root_20260813` SSH 私钥、远端路径 `/root/keyanhuitu/sci-illust-system/web_app/templates/index.html`、systemd 服务 `keyanhuitu`
- Produces: 线上模板更新、`keyanhuitu` 重启后入口可访问

- [ ] **Step 1: Prepare the deployment command**

```bash
scp -i $HOME/.ssh/aliyun_root_20260813 \
  sci-illust-system/web_app/templates/index.html \
  root@47.106.188.244:/root/keyanhuitu/sci-illust-system/web_app/templates/index.html
```

- [ ] **Step 2: Verify remote service before restart**

Run:

```bash
ssh -i $HOME/.ssh/aliyun_root_20260813 root@47.106.188.244 \
  "systemctl is-active keyanhuitu && test -f /root/keyanhuitu/sci-illust-system/web_app/templates/index.html"
```

Expected: `active`

- [ ] **Step 3: Restart minimal service and verify local HTTP**

Run:

```bash
ssh -i $HOME/.ssh/aliyun_root_20260813 root@47.106.188.244 \
  "systemctl restart keyanhuitu && curl -I -s http://127.0.0.1:5000/ | head -n 1"
```

Expected: `HTTP/1.1 200 OK`

- [ ] **Step 4: Verify public entry and new shell markers**

Run:

```bash
ssh -i $HOME/.ssh/aliyun_root_20260813 root@47.106.188.244 \
  "curl -I -s http://127.0.0.1/ | head -n 1 && \
   curl -s http://127.0.0.1:5000/ | grep -o 'class=\"app workbench-shell\"' | head -n 1"
```

Expected: `HTTP/1.1 200 OK` 且输出 `class="app workbench-shell"`

- [ ] **Step 5: Commit deployment-ready state**

```bash
git add sci-illust-system/web_app/templates/index.html tests/test_deployment_artifacts.py tests/test_platform_navigation.py
git commit -m "feat(web): ship workbench shell redesign"
```

## Self-Review

- 覆盖性检查：spec 中的左侧导航、顶部栏、应用平台主框架、最小 JS 调整、测试和部署验证均已映射到 Task 1-6。
- 占位检查：无 `TODO`、`TBD` 或“稍后实现”字样；每个任务都有明确文件、测试入口和命令。
- 接口一致性：所有任务持续使用现有 `draw-input`、`draw-submit`、`workflow-submit`、`download-svg`、`switchPage(name)`，新增类名与 spec 一致。
