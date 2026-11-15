import { msg, setText, serviceText, initI18n, dateText } from "./i18n.js";
import { initPapers, loadPapers } from "./papers.js";
import { initShell, selectView, loadTree, readerOpened } from "./shell.js";
import { request, runJob } from "./api.js";
import {
  $,
  element,
  keyValues,
  guarded,
  busy,
  notify,
  progress,
  contentTypeLabel,
  action,
  strategyLabel,
} from "./ui.js";
import { initDocuments, loadDocuments, loadRecent, openReader } from "./documents.js";
import { initResearch, loadHistory, loadEvaluations, renderAnswer, renderEvaluation } from "./research.js";

const views = new Set([
  "papers",
  "overview",
  "documents",
  "search",
  "ask",
  "history",
  "evaluation",
  "settings",
  "reader",
]);
let defaultsApplied = false;
export function navigate(name) {
  if (!views.has(name)) name = "overview";
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== name;
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
    button.setAttribute(
      "aria-current",
      button.dataset.view === name ? "page" : "false",
    );
  });
  history.replaceState(null, "", `#${name}`);
  selectView(name);
  const loaders = {
    papers: loadPapers,
    overview: refreshOverview,
    documents: loadDocuments,
    history: async () => { await loadHistory(navigate); await loadJobs(); },
    evaluation: loadEvaluations,
    settings: loadConfiguration,
  };
  if (loaders[name]) loaders[name]().catch((error) => notify(serviceText(error.message)));
}

async function refreshOverview() {
  const data = await request("/api/dashboard");
  $("stats").replaceChildren();
  for (const [label, key, note] of [
    [msg("已收录文献"), "documents", msg("原文与出处关联保存")],
    [msg("已保存文本块"), "chunks", msg("待补充条目不参与检索")],
    [msg("已建立向量"), "vectors", msg("独立记录模型指纹")],
    [msg("已保存问答"), "questions", msg("包含当次证据快照")],
  ]) {
    const card = element("article", "stat");
    card.append(
      element("span", "stat-label", label),
      element("strong", "stat-number", data.stats[key]),
      element("span", "stat-note", note),
    );
    $("stats").append(card);
  }
  updateConfiguration(data.configuration);
  await loadRecent();
}

function updateConfiguration(config) {
  if (!defaultsApplied) {
    for (const prefix of ["search", "ask"]) {
      $(`${prefix}-strategy`).value = config.retrieval;
      const count = $(`${prefix}-top-k`);
      if (
        ![...count.options].some(
          (option) => option.value === String(config.top_k),
        )
      )
        count.add(new Option(config.top_k, config.top_k));
      count.value = config.top_k;
    }
    defaultsApplied = true;
  }
  const answerMode =
    config.answer_mode === "generated" ? msg("生成式问答已配置") : msg("原文证据模式");
  setText($("mode-badge"), answerMode);
  $("overview-config").replaceChildren(
    keyValues([
      [msg("问答方式"), answerMode],
      [
        msg("向量模型"),
        config.embedding_backend === "hash"
          ? msg("本地哈希基线")
          : config.embedding_model,
      ],
      [msg("默认检索"), strategyLabel(config.retrieval)],
      [
        msg("文本块长度"),
        msg("{size} 字符 / 重叠 {overlap}", {size: config.chunk_size, overlap: config.chunk_overlap}),
      ],
    ]),
  );
  $("configuration-table").replaceChildren(
    keyValues([
      [msg("问答方式"), answerMode],
      [msg("生成服务"), config.llm_backend],
      [msg("生成模型"), config.llm_model || msg("未配置")],
      [msg("向量服务"), config.embedding_backend],
      [msg("向量模型"), config.embedding_model],
      [msg("向量密钥"), config.embedding_key_configured ? msg("已配置") : msg("未配置")],
      [msg("生成密钥"), config.llm_key_configured ? msg("已配置") : msg("未配置")],
      [msg("默认检索"), strategyLabel(config.retrieval)],
      [msg("默认返回片段"), config.top_k],
      [msg("召回候选片段"), config.retrieval_candidates],
      [msg("每篇文献证据上限"), config.max_evidence_per_document],
      [msg("重排序模型"), config.reranker_model || msg("未配置")],
      [msg("调用超时"), msg("{seconds} 秒", {seconds: config.request_timeout})],
      [msg("访问令牌"), config.authentication_required ? msg("已启用") : msg("本机访问")],
    ]),
  );
}
async function loadConfiguration() {
  const { configuration } = await request("/api/config");
  updateConfiguration(configuration);
}

async function loadJobs() {
  const {items} = await request("/api/jobs");
  $("jobs-list").replaceChildren();
  for (const job of items) {
    const row = element("div", "job-row");
    const kind = msg({ingest: "文献入库", answer: "证据问答", evaluation: "检索评估", reindex: "索引维护"}[job.kind] || job.kind);
    row.append(element("p", "", msg("{kind} · {stage} · {date}", {kind, stage: serviceText(job.stage), date: dateText(job.created_at * 1000)})));
    if (job.error) row.append(element("p", "notice", serviceText(job.error)));
    if (["failed", "interrupted"].includes(job.status)) row.append(action(msg("重新提交"), async (event) => {
      if (job.kind === "ingest" || !job.retry) { navigate("documents"); return; }
      await busy(event.target, async () => {
        const result = await runJob(`/api/jobs/${encodeURIComponent(job.id)}/retry`, {}, current => {
          setText(row.querySelector("p"), msg("{kind} · {stage} · {progress}%", {kind, stage: serviceText(current.stage), progress: current.progress}));
        });
        if (job.kind === "answer") { navigate("ask"); renderAnswer(result); }
        else if (job.kind === "evaluation") { navigate("evaluation"); renderEvaluation(result); }
        notify(msg("重新提交的任务已完成"));
        await loadJobs();
        document.dispatchEvent(new Event("biokb:data-changed"));
      });
    }));
    $("jobs-list").append(row);
  }
  if (!items.length) $("jobs-list").append(element("p", "empty", msg("暂无后台任务")));
}

async function loadFacets() {
  const {facets} = await request("/api/facets");
  for (const prefix of ["documents", "search", "ask"]) {
    for (const key of ["topic", "year", "content_type"]) {
      const select = $(`${prefix}-${key}`);
      const chosen = select.value;
      while (select.options.length > 1) select.remove(1);
      for (const value of facets[key]) {
        const option = element("option", "", key === "content_type" ? contentTypeLabel(value) : value);
        option.value = value;
        select.add(option);
      }
      select.value = chosen;
    }
  }
}

document
  .querySelectorAll("[data-view]")
  .forEach((button) =>
    button.addEventListener("click", () => navigate(button.dataset.view)),
  );
document
  .querySelectorAll("[data-go]")
  .forEach((button) =>
    button.addEventListener("click", () => navigate(button.dataset.go)),
  );
window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
document.addEventListener("biokb:unauthorized", () => {
  $("access-panel").hidden = false;
});
document.addEventListener("biokb:data-changed", guarded(refreshOverview));
document.addEventListener("biokb:data-changed", guarded(loadFacets));
document.addEventListener("biokb:data-changed", guarded(loadTree));
document.addEventListener("biokb:open-reader", guarded(event => openReader(event.detail)));
document.addEventListener("biokb:reader-loaded", event => readerOpened(event.detail));
$("access-form").addEventListener(
  "submit",
  guarded(async () => {
    sessionStorage.setItem("biokb_access_token", $("access-token").value);
    await loadConfiguration();
    await loadFacets();
    await loadTree();
    $("access-token").value = "";
    $("access-panel").hidden = true;
    navigate(location.hash.slice(1));
  }),
);
$("rebuild-index").addEventListener(
  "click",
  guarded(async (event) => {
    await busy(event.target, async () => {
      const result = await runJob(
        "/api/index/rebuild",
        {},
        progress("rebuild"),
      );
      setText($("rebuild-status"), msg("索引完成，共 {count} 个文本块", {count: result.chunks}));
      await refreshOverview();
    });
  }),
);
initI18n();
initShell(navigate);
initDocuments();
initResearch(navigate);
initPapers();
document.addEventListener("biokb:navigate", event => navigate(event.detail));
for (const key of ["topic", "year", "content_type"]) $(`documents-${key}`).addEventListener("change", guarded(loadDocuments));
loadFacets().catch(error => { if (error.code !== "unauthorized") notify(serviceText(error.message)); });
loadTree().catch(error => { if (error.code !== "unauthorized") notify(serviceText(error.message)); });
navigate(location.hash.slice(1));
