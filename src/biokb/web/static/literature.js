/** 官方文献发现：先查看来源与摘要，再按许可入库。 */
import { request, runJob } from "./api.js";
import { $, element, action, busy, guarded, safeLink } from "./ui.js";
import { msg, setText, serviceText } from "./i18n.js";

let page = 1;
let lastQuery = null;
async function search() {
  const query = lastQuery || {
    topic: $("literature-topic").value,
    keywords: $("literature-keywords").value,
    year_from: Number($("literature-from").value),
    year_to: Number($("literature-to").value),
    open_access: $("literature-open").checked,
  };
  lastQuery = query;
  setText($("literature-status"), msg("正在检索 Europe PMC…"));
  const result = await request("/api/literature/search", {method: "POST", body: {...query, page, page_size: 10}});
  $("literature-results").replaceChildren();
  setText($("literature-status"), msg("找到 {count} 篇论文，当前第 {page} 页", {count: result.total, page}));
  setText($("literature-query"), result.query);
  for (const paper of result.items) {
    const card = element("article", "card literature-card");
    const meta = element("div", "result-meta");
    meta.append(element("span", "source-tag", "Europe PMC / PubMed"), element("span", "", `${paper.year} · PMID ${paper.pmid}`));
    if (paper.open_access) meta.append(element("span", "badge", msg("开放获取")));
    card.append(meta, element("h2", "", paper.title), element("p", "muted", `${paper.authors} · ${paper.journal}`));
    const abstract = element("details", "abstract-disclosure");
    abstract.append(element("summary", "", msg("查看摘要")), element("p", "result-text", paper.abstract || msg("当前记录未提供摘要")));
    const actions = element("div", "result-actions");
    actions.append(safeLink(paper.url, msg("打开官方原文")), action(msg("导入知识库"), async event => {
      await busy(event.target, async () => {
        const imported = await runJob("/api/literature/import", {pmid: paper.pmid, topic: query.topic, include_fulltext: $("literature-fulltext").checked}, job => setText($("literature-status"), serviceText(job.stage)));
        setText(event.target, imported.duplicate ? msg("已在知识库中") : msg("入库完成"));
        event.target.disabled = true;
        setText($("literature-status"), imported.imported_type === "fulltext" ? msg("已导入开放全文，可前往文献库检索与提问") : msg("已导入摘要；全文未获取或许可不满足自动入库要求"));
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
  $("literature-next").disabled = page * result.page_size >= result.total;
}

export function initLiterature() {
  $("literature-form").addEventListener("submit", guarded(async event => {
    page = 1; lastQuery = null;
    await busy(event.submitter, search);
  }));
  $("literature-prev").addEventListener("click", guarded(async event => {page -= 1; await busy(event.target, search);}));
  $("literature-next").addEventListener("click", guarded(async event => {page += 1; await busy(event.target, search);}));
}
