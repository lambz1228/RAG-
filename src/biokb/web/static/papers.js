/** 论文目录、资料版本和显式研究范围。 */
import { msg, setText } from "./i18n.js";
import { request } from "./api.js";
import { $, element, action, guarded, contentTypeLabel, safeLink, notify } from "./ui.js";

const selected = new Map();
const state = {offset: 0, limit: 12, sequence: 0, total: 0};
const topicLabels = {
  peroxisome: "过氧化物酶体", er_mito_contact: "内质网与线粒体接触", mitosis: "有丝分裂",
  cytoskeleton: "细胞骨架", lysosome: "溶酶体", golgi: "高尔基体", mitochondria: "线粒体",
  nucleolus_ribosome: "核仁与核糖体", er_stress: "内质网应激", cilia: "纤毛",
  autophagy: "自噬", mitophagy: "线粒体自噬",
};
export const topicName = value => {
  const declared = [...document.querySelectorAll("#literature-topic option")].find(option => option.value === value);
  const label = topicLabels[value] || declared?.dataset.i18n;
  return label ? msg(label) : value;
};
export const paperScope = () => [...selected.keys()];

export function scopeDescription(scope) {
  if (!scope) return element("div", "notice", msg("历史记录未保存研究范围"));
  const titles = scope.kind === "papers" ? scope.papers.map(p => p.title).join("；") : scope.document_id || "";
  const description = titles ? msg("选定资料：{titles}", {titles}) : msg("全部可用资料");
  const filters = Object.entries(scope.filters || {}).map(([key, value]) => key === "topic" ? topicName(value) : key === "content_type" ? contentTypeLabel(value) : value);
  const node = element("div", "notice");
  node.append(element("span", "", description));
  if (filters.length) {
    node.append(element("span", "", msg("；同时应用筛选：")));
    for (const filter of filters) node.append(element("span", "scope-filter", filter));
  }
  return node;
}

function renderScope() {
  for (const prefix of ["papers", "search", "ask"]) {
    const target = $(`${prefix}-scope`);
    target.replaceChildren(element("span", "scope-label", selected.size ? msg("研究范围 · {count} 篇论文", {count: selected.size}) : msg("全部可用资料")));
    for (const paper of selected.values()) {
      const chip = action(paper.title + " ×", () => {
        selected.delete(paper.id); renderScope();
        document.querySelectorAll(`[data-paper-id="${paper.id}"]`).forEach(node => { node.checked = false; });
      }, "scope-chip");
      chip.title = paper.title;
      target.append(chip);
    }
    if (selected.size) target.append(action(msg("清空研究范围"), () => {
      selected.clear(); renderScope();
      document.querySelectorAll("[data-paper-id]").forEach(node => { node.checked = false; });
    }));
  }
}

export function usePaper(paper, view = "ask") {
  selected.clear(); selected.set(paper.id, paper); renderScope();
  for (const prefix of ["search", "ask"]) for (const key of ["topic", "year", "content_type"]) $(`${prefix}-${key}`).value = "";
  if (view === "ask") $("ask-top-k").value = "10";
  document.dispatchEvent(new CustomEvent("biokb:navigate", {detail: view}));
  $(`${view}-query`).focus();
}

export async function loadPapers() {
  const sequence = ++state.sequence;
  const params = new URLSearchParams({q: $("papers-query").value, topic: $("papers-topic").value,
    year: $("papers-year").value, fulltext: String($("papers-fulltext").checked), limit: state.limit, offset: state.offset});
  const data = await request(`/api/papers?${params}`);
  if (sequence !== state.sequence) return;
  if (!data.items.length && state.offset > 0) {state.offset = 0; return loadPapers();}
  state.total = data.total;
  const summary = $("papers-summary");
  summary.replaceChildren();
  for (const [key, label] of [["papers", "论文条目"], ["fulltext_papers", "具有全文"], ["abstract_only_papers", "仅有摘要"], ["reference_stubs", "待补充资料"]]) {
    const stat = element("div", "stat");
    stat.append(element("span", "stat-label", msg(label)), element("strong", "stat-number", data.summary[key]));
    summary.append(stat);
  }
  for (const [key, values] of [["topic", data.topics], ["year", data.years]]) {
    const select = $(`papers-${key}`), chosen = select.value;
    while (select.options.length > 1) select.remove(1);
    for (const value of values) {const option = element("option", "", key === "topic" ? topicName(value) : value); option.value = value; select.add(option);}
    select.value = chosen;
  }
  const target = $("papers-list");
  target.replaceChildren();
  if (!data.items.length) target.append(element("div", "empty card", msg("暂无匹配论文")));
  for (const paper of data.items) {
    const card = element("article", "card paper-card");
    const heading = element("div", "paper-heading");
    const select = element("label", "checkbox-label");
    const checkbox = element("input"); checkbox.type = "checkbox"; checkbox.checked = selected.has(paper.id);
    checkbox.dataset.paperId = paper.id; checkbox.setAttribute("aria-label", paper.title);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked && selected.size >= 20) {checkbox.checked = false; notify(msg("一次最多选择 20 篇论文")); return;}
      if (checkbox.checked) selected.set(paper.id, paper); else selected.delete(paper.id);
      renderScope();
    });
    select.append(checkbox, element("span", "sr-only", msg("加入研究范围")));
    heading.append(select, element("h2", "", paper.title));
    card.append(heading, element("p", "muted", [Array.isArray(paper.authors) ? paper.authors.join(", ") : paper.authors, paper.journal, paper.year].filter(Boolean).join(" · ")));
    const identifiers = element("p", "muted");
    for (const [key, value] of Object.entries(paper.identifiers)) identifiers.append(element("span", "paper-identifier", `${key.toUpperCase()}: ${value}`));
    card.append(identifiers);
    const topics = element("div", "paper-topics");
    for (const value of paper.topics) topics.append(element("span", "badge", topicName(value)));
    card.append(topics, element("p", "muted", paper.has_fulltext ? msg("已关联摘要与全文资料，可回到各版本原文。") : msg("当前仅有摘要，可能无法支持实验细节问题。")));
    if (paper.identity_conflict) card.append(element("p", "notice", msg("正式标识存在冲突，已保留为独立条目，需人工核对。")));
    const actions = element("div", "result-actions");
    actions.append(action(msg("针对这篇论文提问"), () => usePaper(paper)), action(msg("检索这篇论文"), () => usePaper(paper, "search")));
    for (const record of paper.records) actions.append(action(contentTypeLabel(record.content_type), () => document.dispatchEvent(new CustomEvent("biokb:open-reader", {detail: record.id}))));
    if (paper.records[0]?.url) actions.append(safeLink(paper.records[0].url, msg("打开来源")));
    card.append(actions); target.append(card);
  }
  setText($("papers-page"), msg("共 {count} 篇 · 第 {page} 页", {count: data.total, page: Math.floor(state.offset / state.limit) + 1}));
  $("papers-prev").disabled = state.offset === 0;
  $("papers-next").disabled = state.offset + state.limit >= data.total;
}

export function initPapers() {
  $("papers-filter").addEventListener("submit", guarded(() => {state.offset = 0; return loadPapers();}));
  for (const key of ["topic", "year", "fulltext"]) $(`papers-${key}`).addEventListener("change", guarded(() => {state.offset = 0; return loadPapers();}));
  $("papers-prev").addEventListener("click", guarded(() => {state.offset = Math.max(0, state.offset - state.limit); return loadPapers();}));
  $("papers-next").addEventListener("click", guarded(() => {state.offset += state.limit; return loadPapers();}));
  $("papers-ask").addEventListener("click", () => document.dispatchEvent(new CustomEvent("biokb:navigate", {detail: "ask"})));
  document.addEventListener("biokb:use-paper", event => usePaper(event.detail));
  renderScope();
}
