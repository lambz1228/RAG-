import { msg, setText, serviceText, dateText, applyTranslations } from "./i18n.js";
/** 通用界面组件。所有文献和模型文本使用 textContent，避免解释为 HTML。 */
export const $ = (id) => document.getElementById(id);
export function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") setText(node, text);
  return node;
}
export function notify(message) {
  setText($("toast"), message);
  $("toast").hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => {
    $("toast").hidden = true;
  }, 6000);
}
export function guarded(action) {
  return (event) => {
    if (event?.type === "submit") event.preventDefault();
    Promise.resolve()
      .then(() => action(event))
      .catch((error) => notify(serviceText(error.message) || msg("操作失败")));
  };
}
export async function busy(button, operation) {
  const old = button.cloneNode(true);
  button.disabled = true;
  setText(button, msg("处理中…"));
  try {
    return await operation();
  } catch (error) {
    const container = button.closest("form, article");
    const status = container?.querySelector('[role="status"]');
    if (status)
      setText(status, msg("操作失败：{error}", {error: serviceText(error.message) || msg("请稍后重试")}));
    const bar = container?.querySelector("progress");
    if (bar) bar.hidden = true;
    throw error;
  } finally {
    button.disabled = false;
    delete button.dataset.i18n;
    delete button.dataset.i18nParams;
    if (old.dataset.i18n) setText(button, msg(old.dataset.i18n, JSON.parse(old.dataset.i18nParams || "{}")));
    else { button.replaceChildren(...old.childNodes); applyTranslations(button); }
  }
}
export function progress(prefix) {
  const bar = $(`${prefix}-progress`);
  return (job) => {
    setText($(`${prefix}-status`), job.status === "failed"
      ? msg("处理失败：{error}", {error: serviceText(job.error)})
      : msg("{stage}{progress}", {stage: serviceText(job.stage), progress: job.status === "completed" ? "" : ` · ${job.progress}%`}));
    if (bar) {
      bar.hidden = false;
      bar.value = job.progress;
    }
  };
}
export function warnings(target, values = []) {
  for (const value of values) target.append(element("div", "notice", serviceText(value)));
}
export function table(headers, rows) {
  const wrapper = element("div", "table-wrap");
  const node = element("table", "data-table");
  const head = document.createElement("thead");
  const tr = document.createElement("tr");
  for (const title of headers) tr.append(element("th", "", title));
  head.append(tr);
  const body = document.createElement("tbody");
  for (const row of rows) {
    const line = document.createElement("tr");
    for (const item of row) {
      const cell = document.createElement("td");
      cell.append(
        item instanceof Node
          ? item
          : element("span", "", item ?? "—"),
      );
      line.append(cell);
    }
    body.append(line);
  }
  node.append(head, body);
  wrapper.append(node);
  return wrapper;
}
export function keyValues(items) {
  const node = element("dl", "kv");
  for (const [key, value] of items)
    node.append(element("dt", "", key), element("dd", "", value));
  return node;
}
export function action(text, callback, className = "text-button") {
  const button = element("button", className, text);
  button.type = "button";
  button.addEventListener("click", guarded(callback));
  return button;
}
export const dateLabel = dateText;
export function safeLink(url, label) {
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol))
      return element("span", "", label);
    const link = element("a", "", label);
    link.href = parsed.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch {
    return element("span", "", label);
  }
}
export function evidenceCard(hit, index, openDocument) {
  const card = element("article", "card result-card");
  card.dataset.evidenceNumber = String(hit.number || index + 1);
  card.tabIndex = -1;
  const meta = element("div", "result-meta");
  meta.append(
    element("span", "badge", msg("证据 {number}", {number: hit.number || index + 1})),
    element("span", "", serviceText(hit.locator)),
    element("span", "", contentTypeLabel(hit.metadata?.content_type)),
  );
  if (hit.score != null)
    meta.append(
      element("span", "", msg("检索得分 {score}", {score: Number(hit.score).toFixed(4)})),
    );
  card.append(
    meta,
    element("h3", "", hit.title),
    element("div", "result-text", hit.text),
  );
  const actions = element("div", "result-actions");
  actions.append(
    action(msg("查看文献与文本块"), () => openDocument(hit.document_id, hit.chunk_id, hit.context_chunk_ids || [])),
  );
  if (hit.metadata?.url) actions.append(safeLink(hit.metadata.url, msg("打开来源")));
  if (hit.metadata?.journal)
    actions.append(element("span", "muted", hit.metadata.journal));
  card.append(actions);
  return card;
}

export function contentTypeLabel(value) {
  return {abstract: msg("论文摘要"), fulltext: msg("全文"), knowledge_note: msg("知识条目"), reference_stub: msg("待补充")}[value] || msg("类型未标注");
}

export function readFilters(prefix) {
  return Object.fromEntries(["topic", "year", "content_type"].map(key => [key, $(`${prefix}-${key}`)?.value || ""]).filter(([, value]) => value));
}

export function strategyLabel(value) {
  return msg({bm25: "BM25 关键词检索", dense: "向量检索", hybrid: "混合检索", hybrid_rerank: "混合检索＋重排序"}[value] || value);
}
