/** 工作台导航、标签页与按需加载的文献树。 */
import { request } from "./api.js";
import { $, element, action, guarded, contentTypeLabel } from "./ui.js";
import { msg, t, setText, serviceText, applyTranslations } from "./i18n.js";

const labels = {sources: "文献采集", papers: "科研文献", overview: "知识库概览", documents: "文献管理", search: "知识检索", ask: "证据问答", history: "问答记录", evaluation: "检索评估", settings: "运行配置", reader: "文献阅读"};
const icons = {
  papers: "M4 3h11l5 5v13H4zM14 3v6h6M8 13h8M8 17h6",
  overview: "M3 10 12 3 21 10M5 9v12h14V9M9 21v-7h6v7",
  documents: "M4 3h11l5 5v13H4zM14 3v6h6M8 13h8M8 17h6",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14M15 15l6 6",
  ask: "M3 4h18v13H8l-5 4zM7 8h10M7 12h7",
  history: "M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v6l4 2",
  evaluation: "M4 3v18h17M8 16v-4M13 16V7M18 16v-7",
  settings: "M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6",
  panel: "M3 4h18v16H3zM9 4v16", info: "M3 4h18v16H3zM16 4v16",
  plus: "M12 5v14M5 12h14", folder: "M3 6h7l2 3h9v11H3z",
  leaf: "M20 3C8 2 2 8 5 16c9 5 16-1 15-13ZM5 20l10-11", reader: "M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3zM12 6v16",
};
export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", icons[name] || icons.documents);
  svg.append(path);
  return svg;
}

const openTabs = ["overview"];
let activeView = "overview";
let route;
let treeSequence = 0;
let readerTitle = null;
const expanded = new Set(["abstract"]);

function renderTabs() {
  const target = $("workspace-tabs");
  target.replaceChildren();
  for (const view of openTabs) {
    const item = element("div", "tab-item" + (view === activeView ? " selected" : ""));
    const tab = action("", () => route(view), "tab-button");
    tab.id = `tab-${view}`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(view === activeView));
    tab.setAttribute("aria-controls", `panel-${view}`);
    tab.tabIndex = view === activeView ? 0 : -1;
    tab.append(icon(view), element("span", "tab-label", view === "reader" && readerTitle ? readerTitle : msg(labels[view])));
    tab.addEventListener("keydown", event => {
      const index = openTabs.indexOf(view);
      let next;
      if (event.key === "ArrowRight") next = openTabs[(index + 1) % openTabs.length];
      if (event.key === "ArrowLeft") next = openTabs[(index + openTabs.length - 1) % openTabs.length];
      if (event.key === "Home") next = openTabs[0];
      if (event.key === "End") next = openTabs.at(-1);
      if (next) { event.preventDefault(); route(next); $(`tab-${next}`).focus(); }
    });
    item.append(tab);
    if (view !== "overview") {
      const close = action("×", () => {
        const index = openTabs.indexOf(view);
        openTabs.splice(index, 1);
        if (activeView === view) route(openTabs[Math.max(0, index - 1)]);
        else renderTabs();
        $(`tab-${activeView}`)?.focus();
      }, "tab-close");
      close.dataset.i18nAriaLabel = "关闭标签页";
      close.setAttribute("aria-label", t("关闭标签页"));
      item.append(close);
    }
    target.append(item);
  }
  $(`tab-${activeView}`)?.scrollIntoView({block: "nearest", inline: "nearest"});
}

export function selectView(name) {
  activeView = name;
  if (!openTabs.includes(name)) openTabs.push(name);
  renderTabs();
  setText($("breadcrumb-current"), name === "reader" && readerTitle ? readerTitle : msg(labels[name]));
  setText($("footer-view"), msg(labels[name]));
  if (matchMedia("(max-width: 760px)").matches) toggleSidebar(false);
  updateOutline();
}

export function readerOpened(doc) {
  readerTitle = doc.title;
  route("reader");
}

export function updateOutline() {
  const target = $("page-outline");
  if (!target) return;
  target.replaceChildren();
  const panel = document.querySelector(`[data-panel="${activeView}"]`);
  for (const heading of panel?.querySelectorAll("h2") || []) {
    const label = heading.dataset.i18n
      ? msg(heading.dataset.i18n, JSON.parse(heading.dataset.i18nParams || "{}"))
      : heading.textContent;
    target.append(action(label, () => {
      heading.tabIndex = -1;
      heading.scrollIntoView({behavior: "smooth", block: "start"});
      heading.focus({preventScroll: true});
    }, "outline-link"));
  }
  if (!target.children.length) target.append(element("p", "muted", msg("当前页面没有章节")));
}

function toggleSidebar(force) {
  const mobile = matchMedia("(max-width: 760px)").matches;
  if (mobile) document.body.classList.toggle("navigation-open", force ?? !document.body.classList.contains("navigation-open"));
  else document.body.classList.toggle("navigation-collapsed", force === undefined ? undefined : !force);
  const open = mobile ? document.body.classList.contains("navigation-open") : !document.body.classList.contains("navigation-collapsed");
  $("toggle-sidebar").setAttribute("aria-expanded", String(open));
  $("navigation-shade").hidden = !mobile || !open;
}

export async function loadTree() {
  const version = ++treeSequence;
  const target = $("document-tree");
  const query = $("tree-query").value.trim();
  target.replaceChildren(element("p", "tree-message", msg("正在读取文献")));
  const appendPage = async (container, type = "", offset = 0) => {
    const params = new URLSearchParams({limit: 40, offset, q: query});
    if (type) params.set("content_type", type);
    const data = await request(`/api/documents?${params}`);
    if (version !== treeSequence || !container.isConnected) return;
    container.querySelector(".tree-more")?.remove();
    if (offset === 0) container.replaceChildren();
    for (const doc of data.items) {
      const button = action("", () => document.dispatchEvent(new CustomEvent("biokb:open-reader", {detail: doc.id})), "tree-document");
      button.append(icon("documents"), element("span", "", doc.title));
      button.title = doc.title;
      container.append(button);
    }
    if (!data.total) container.append(element("p", "tree-message", msg("暂无匹配文献")));
    if (offset + data.items.length < data.total) {
      const more = action(msg("加载更多文献"), async () => {
        more.disabled = true;
        try { await appendPage(container, type, offset + data.items.length); }
        finally { more.disabled = false; }
      }, "tree-more");
      container.append(more);
    }
    const count = container.parentElement.querySelector(".tree-count");
    if (count) setText(count, data.total);
  };
  if (query) { await appendPage(target); return; }
  target.replaceChildren();
  for (const type of ["abstract", "fulltext", "knowledge_note", "reference_stub"]) {
    const group = element("details", "tree-group");
    const summary = element("summary");
    summary.append(icon("folder"), element("span", "", contentTypeLabel(type)), element("span", "tree-count"));
    const children = element("div", "tree-children");
    group.append(summary, children);
    let loaded = false;
    group.addEventListener("toggle", guarded(async () => {
      if (group.open) expanded.add(type); else expanded.delete(type);
      if (!group.open || loaded) return;
      children.replaceChildren(element("p", "tree-message", msg("正在读取文献")));
      try { await appendPage(children, type); loaded = true; }
      catch (error) { children.replaceChildren(element("p", "tree-message", serviceText(error.message))); }
    }));
    target.append(group);
    group.open = expanded.has(type);
  }
}

export function initShell(navigate) {
  route = navigate;
  document.querySelectorAll("[data-icon]").forEach(node => node.replaceChildren(icon(node.dataset.icon)));
  document.querySelectorAll("[data-panel]").forEach(panel => {
    panel.id = `panel-${panel.dataset.panel}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", `tab-${panel.dataset.panel}`);
  });
  $("toggle-sidebar").addEventListener("click", () => toggleSidebar());
  $("navigation-shade").addEventListener("click", () => toggleSidebar(false));
  $("toggle-inspector").addEventListener("click", () => {
    const collapsed = document.body.classList.toggle("inspector-collapsed");
    $("toggle-inspector").setAttribute("aria-expanded", String(!collapsed));
  });
  let timer;
  $("tree-query").addEventListener("input", () => {
    clearTimeout(timer);
    ++treeSequence;
    timer = setTimeout(guarded(loadTree), 240);
  });
  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      toggleSidebar(true);
      $("tree-query").focus();
    }
    if (event.key === "Escape" && document.body.classList.contains("navigation-open")) {
      toggleSidebar(false); $("toggle-sidebar").focus();
    }
  });
  const media = matchMedia("(max-width: 760px)");
  $("toggle-sidebar").setAttribute("aria-expanded", String(!media.matches));
  media.addEventListener("change", () => {
    document.body.classList.remove("navigation-open");
    $("navigation-shade").hidden = true;
    $("toggle-sidebar").setAttribute("aria-expanded", String(!media.matches && !document.body.classList.contains("navigation-collapsed")));
  });
  document.addEventListener("biokb:locale-changed", updateOutline);
  new MutationObserver(updateOutline).observe(document.querySelector("main"), {childList: true, subtree: true});
  applyTranslations();
}
