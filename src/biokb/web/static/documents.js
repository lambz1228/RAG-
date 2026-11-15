import { msg, t, setText, serviceText } from "./i18n.js";
import { request, runJob, download } from "./api.js";
import {
  $,
  element,
  action,
  table,
  keyValues,
  busy,
  guarded,
  notify,
  progress,
  dateLabel,
  safeLink,
  contentTypeLabel,
  readFilters,
} from "./ui.js";

const state = { offset: 0, limit: 20, query: "", total: 0 };
export async function openDocument(id, chunkId = "", contextChunkIds = []) {
  const { document: doc } = await request(
    `/api/documents/${encodeURIComponent(id)}`,
  );
  setText($("document-dialog-title"), doc.title);
  const content = $("document-dialog-body");
  const selected = renderDocument(doc, content, chunkId, "document", contextChunkIds);
  if (!$("document-dialog").open) $("document-dialog").showModal();
  if (selected) selected.scrollIntoView({block: "center"});
}

function renderDocument(doc, content, chunkId = "", prefix = "document", contextChunkIds = []) {
  const id = doc.id;
  content.replaceChildren(
    keyValues([
      [msg("文件名"), doc.title || doc.filename],
      [msg("资料类型"), contentTypeLabel(doc.metadata.content_type)],
      [msg("质量状态"), doc.metadata.quality?.eligible === false
        ? msg("待补充，未用于检索回答")
        : msg("未经人工学术审核")],
    ]),
  );
  const links = element("div", "result-actions");
  links.append(
    action(msg("下载原始文件"), () =>
      download(
        `/api/documents/${encodeURIComponent(id)}/download`,
        doc.filename,
      ),
    ),
  );
  if (doc.metadata.url)
    links.append(safeLink(doc.metadata.url, msg("打开文献来源")));
  if (doc.paper) links.append(action(msg("针对这篇论文提问"), () => {
    if ($("document-dialog").open) $("document-dialog").close();
    document.dispatchEvent(new CustomEvent("biokb:use-paper", {detail: doc.paper}));
  }));
  content.append(links, element("h3", "evidence-heading", msg("原文文本块")));
  for (const reason of doc.metadata.quality?.reasons || []) content.append(element("p", "notice", serviceText(reason)));
  let selected;
  for (const chunk of doc.chunks) {
    const section = element("section");
    section.id = `${prefix}-${chunk.id}`;
    if (chunk.id === chunkId) {
      section.classList.add("selected-evidence");
      selected = section;
    } else if (contextChunkIds.includes(chunk.id)) {
      section.classList.add("context-evidence");
    }
    section.append(
      element("p", "muted", msg("片段 {number} · {position}", {number: chunk.ordinal + 1, position: serviceText(chunk.locator)})),
      element("p", "document-text", chunk.text),
    );
    content.append(section);
  }
  return selected;
}

let readerSequence = 0;
export async function openReader(id) {
  const sequence = ++readerSequence;
  const {document: doc} = await request(`/api/documents/${encodeURIComponent(id)}`);
  if (sequence !== readerSequence) return;
  setText($("reader-title"), doc.title);
  renderDocument(doc, $("reader-body"), "", "reader");
  document.dispatchEvent(new CustomEvent("biokb:reader-loaded", {detail: doc}));
}

function documentRows(items, removable = false) {
  return items.map((doc) => {
    const title = element("div");
    title.append(
      action(doc.title, () => openDocument(doc.id)),
      element("small", "", doc.metadata.topic || doc.filename),
    );
    const row = [
      title,
      msg("{format} · {type}", {format: doc.file_type.replace(".", "").toUpperCase(), type: contentTypeLabel(doc.metadata.content_type)}),
      doc.chunk_count,
      dateLabel(doc.created_at),
    ];
    if (removable)
      row.push(
        action(
          msg("移出知识库"),
          async () => {
            if (
              !confirm(
                t("将《{title}》及其检索索引移出知识库？原始归档和已有问答快照会保留。", {title: doc.title}),
              )
            )
              return;
            await request(`/api/documents/${encodeURIComponent(doc.id)}`, {
              method: "DELETE",
            });
            notify(msg("文献已移出知识库"));
            await loadDocuments();
            document.dispatchEvent(new Event("biokb:data-changed"));
          },
          "text-button",
        ),
      );
    return row;
  });
}

export async function loadRecent() {
  const data = await request("/api/documents?limit=5");
  const target = $("recent-documents");
  delete target.dataset.i18n;
  delete target.dataset.i18nParams;
  target.className = data.items.length ? "" : "empty";
  target.replaceChildren(
    data.items.length
      ? table([msg("文献"), msg("格式"), msg("片段数"), msg("入库时间")], documentRows(data.items))
      : element("span", "", msg("知识库尚无文献，请先导入论文或研究笔记。")),
  );
}

export async function loadDocuments() {
  const params = new URLSearchParams({
    q: state.query,
    limit: state.limit,
    offset: state.offset,
    ...readFilters("documents"),
  });
  const data = await request(`/api/documents?${params}`);
  if (!data.items.length && state.offset > 0) {
    state.offset = Math.max(
      0,
      (Math.ceil(data.total / state.limit) - 1) * state.limit,
    );
    return loadDocuments();
  }
  state.total = data.total;
  setText($("document-total"), msg("{count} 篇", {count: data.total}));
  $("documents-table").replaceChildren(
    data.items.length
      ? table(
          [msg("文献"), msg("格式"), msg("片段数"), msg("入库时间"), msg("操作")],
          documentRows(data.items, true),
        )
      : element("div", "empty", msg("暂无匹配文献")),
  );
  setText($("documents-page"), msg("第 {page} / {pages} 页", {page: Math.floor(state.offset / state.limit) + 1, pages: Math.max(1, Math.ceil(data.total / state.limit))}));
  $("documents-prev").disabled = state.offset === 0;
  $("documents-next").disabled = state.offset + state.limit >= data.total;
}

export function initDocuments() {
  $("close-document").addEventListener("click", () =>
    $("document-dialog").close(),
  );
  $("document-filter").addEventListener(
    "submit",
    guarded(async () => {
      state.query = $("document-query").value;
      state.offset = 0;
      await loadDocuments();
    }),
  );
  $("documents-prev").addEventListener(
    "click",
    guarded(async () => {
      state.offset = Math.max(0, state.offset - state.limit);
      await loadDocuments();
    }),
  );
  $("documents-next").addEventListener(
    "click",
    guarded(async () => {
      state.offset += state.limit;
      await loadDocuments();
    }),
  );
  $("upload-form").addEventListener(
    "submit",
    guarded(async (event) => {
      await busy(event.submitter, async () => {
        const result = await runJob(
          "/api/documents/upload",
          new FormData(event.target),
          progress("upload"),
        );
        setText($("upload-status"), result.duplicate
          ? msg("该文献已经入库，没有重复添加。")
          : msg("入库完成，共 {count} 个文本块。", {count: result.chunk_count}));
        event.target.reset();
        state.offset = 0;
        await loadDocuments();
        document.dispatchEvent(new Event("biokb:data-changed"));
      });
    }),
  );
}
