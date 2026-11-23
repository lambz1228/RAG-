/** 官方文献发现：先查看来源与摘要，再按许可入库。 */
import { request, runJob } from "./api.js";
import { $, element, action, busy, guarded, safeLink } from "./ui.js";
import { msg, setText, serviceText } from "./i18n.js";

let page = 1;
let lastQuery = null;
const cursors = new Map();
async function search(targetPage = 1, newSearch = false) {
  const query = !newSearch && lastQuery || {
    source: $("literature-source").value,
    topic: $("literature-topic").value,
    keywords: $("literature-keywords").value,
    year_from: Number($("literature-from").value),
    year_to: Number($("literature-to").value),
    open_access: !$("literature-open").disabled && $("literature-open").checked,
  };
  const cursor = !newSearch && cursors.get(targetPage);
  setText($("literature-status"), msg("正在检索文献…"));
  const result = await request("/api/literature/search", {method: "POST", body: {...query, page: targetPage, page_size: 10, ...(cursor ? {cursor} : {})}});
  if (newSearch) cursors.clear();
  if (result.next_cursor) cursors.set(targetPage + 1, result.next_cursor);
  lastQuery = query;
  page = targetPage;
  $("literature-results").replaceChildren();
  setText($("literature-status"), msg("找到 {count} 篇论文，当前第 {page} 页", {count: result.total, page}));
  setText($("literature-query"), result.query);
  for (const paper of result.items) {
    const card = element("article", "card literature-card");
    const meta = element("div", "result-meta");
    meta.append(element("span", "source-tag", result.source), element("span", "", `${paper.year} · PMID ${paper.pmid}`));
    if (paper.open_access) meta.append(element("span", "badge", msg("开放获取")));
    card.append(meta, element("h2", "", paper.title), element("p", "muted", `${paper.authors} · ${paper.journal}`));
    const abstract = element("details", "abstract-disclosure");
    abstract.append(element("summary", "", msg("查看摘要")), element("p", "result-text", paper.abstract || msg("当前记录未提供摘要")));
    const actions = element("div", "result-actions");
    actions.append(safeLink(paper.url, msg("打开官方原文")), action(msg("导入知识库"), async event => {
      await busy(event.target, async () => {
        const imported = await runJob("/api/literature/import", {source: query.source, pmid: paper.pmid, topic: query.topic, include_fulltext: query.source === "europe_pmc" && $("literature-fulltext").checked}, job => setText($("literature-status"), serviceText(job.stage)));
        setText(event.target, imported.duplicate ? msg("已在知识库中") : msg("入库完成"));
        event.target.disabled = true;
        const status = {
          fulltext: msg("已导入开放全文，可前往文献库检索与提问"),
          abstract: msg("已导入摘要；全文未获取或许可不满足自动入库要求"),
          reference_stub: msg("已保存题录，待补充摘要"),
        };
        setText($("literature-status"), status[imported.imported_type]);
        document.dispatchEvent(new Event("biokb:data-changed"));
      });
      setText(event.target, msg("已在知识库中"));
      event.target.disabled = true;
    }, "secondary"));
    card.append(abstract, actions);
    $("literature-results").append(card);
  }
  if (!result.items.length) $("literature-results").append(element("div", "empty", msg("没有找到论文，请调整关键词或年份")));
  $("literature-prev").disabled = page <= 1;
  $("literature-next").disabled = page >= 100 || page * result.page_size >= result.total || (query.source === "europe_pmc" && !result.next_cursor);
}

export function initLiterature() {
  const updateSourceOptions = () => {
    const pubmed = $("literature-source").value === "pubmed";
    $("literature-open").disabled = pubmed;
    $("literature-fulltext").disabled = pubmed;
  };
  $("literature-source").addEventListener("change", updateSourceOptions);
  updateSourceOptions();
  $("literature-export-form").addEventListener("submit", guarded(async event => {
    const form = event.currentTarget;
    await busy(event.submitter, async () => {
      const status = $("literature-export-status");
      const errors = $("literature-export-errors");
      errors.replaceChildren();
      errors.hidden = true;
      const result = await runJob(form.getAttribute("action"), new FormData(form), job => setText(status, serviceText(job.stage)));
      setText(status, msg("新增摘要 {abstracts} 条，题录 {reference_stubs} 条，重复 {duplicates} 条，失败 {failed} 条", {...result, failed: result.failed.length}));
      for (const row of result.failed) {
        const item = element("li");
        setText(item, msg("第 {record} 条：{error}", {record: row.record, error: serviceText(row.error)}));
        errors.append(item);
      }
      errors.hidden = !result.failed.length;
      if (result.abstracts || result.reference_stubs) document.dispatchEvent(new Event("biokb:data-changed"));
    });
  }));
  $("literature-form").addEventListener("submit", guarded(async event => {
    await busy(event.submitter, () => search(1, true));
  }));
  for (const [id, step] of [["literature-prev", -1], ["literature-next", 1]]) {
    $(id).addEventListener("click", guarded(async () => {
      const prev = $("literature-prev"), next = $("literature-next");
      const previousState = [prev.disabled, next.disabled];
      prev.disabled = next.disabled = true;
      try {
        await search(page + step);
      } catch (error) {
        [prev.disabled, next.disabled] = previousState;
        setText($("literature-status"), msg("操作失败：{error}", {error: serviceText(error.message)}));
        throw error;
      }
    }));
  }
}
