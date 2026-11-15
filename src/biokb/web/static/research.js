import { msg, t, setText, serviceText } from "./i18n.js";
import { request, runJob, download } from "./api.js";
import {
  $,
  element,
  action,
  evidenceCard,
  warnings,
  busy,
  guarded,
  progress,
  dateLabel,
  table,
  readFilters,
  strategyLabel,
} from "./ui.js";
import { paperScope, scopeDescription } from "./papers.js";
import { openDocument } from "./documents.js";

export const modes = {
  extractive: msg("原文证据摘录"),
  generated: msg("模型依据证据生成"),
  partial: msg("部分问题有证据支持"),
  insufficient_evidence: msg("证据不足"),
  provider_fallback: msg("模型不可用 · 展示原文"),
};

export function renderAnswer(result, container = null) {
  const target = container || $("answer-result");
  target.replaceChildren();
  const card = element("article", "card");
  const header = element("div", "answer-header");
  header.append(
    element("h2", "", msg("回答")),
    element(
      "span",
      result.mode === "generated" ? "badge" : "badge warning",
      modes[result.mode] || result.mode,
    ),
  );
  const text = element("div", "answer-text");
  const parts = result.answer.split(/(\[\d+\])/g);
  for (const part of parts) {
    const number = /^\[(\d+)\]$/.exec(part);
    if (number && result.citations.some(hit => hit.number === Number(number[1]))) {
      text.append(action(part, () => {
        const evidence = target.querySelector(`[data-evidence-number="${number[1]}"]`);
        evidence?.scrollIntoView({behavior: "smooth", block: "center"});
        evidence?.focus({preventScroll: true});
      }, "citation-link"));
    } else text.append(document.createTextNode(part));
  }
  card.append(header, scopeDescription(result.retrieval?.scope), text);
  warnings(card, result.warnings);
  const meta = element("div", "result-actions");
  meta.append(
    element(
      "span",
      "muted",
      msg("用时 {seconds} 秒 · {strategy}", {seconds: (result.latency_ms / 1000).toFixed(2), strategy: strategyLabel(result.retrieval.strategy)}),
    ),
    action(msg("导出回答与证据"), () =>
      download(`/api/history/${result.id}/export`, `${result.id}.json`),
    ),
  );
  card.append(meta);
  target.append(card);
  if (result.citations.length)
    target.append(
      element(
        "h2",
        "evidence-heading",
        msg("引用与证据 · {count} 个片段", {count: result.citations.length}),
      ),
    );
  result.citations.forEach((hit, index) =>
    target.append(evidenceCard(hit, index, openDocument)),
  );
}

export async function loadHistory(navigate) {
  const { items } = await request("/api/history");
  const target = $("history-list");
  target.replaceChildren();
  if (!items.length) target.append(element("div", "empty", msg("暂无问答记录")));
  for (const item of items) {
    const row = element("div", "history-item");
    const copy = element("div");
    copy.append(
      action(item.query, async () => {
        const { result } = await request(`/api/history/${item.id}`);
        navigate("ask");
        // 打开历史回答时退出会话，避免与对话气泡混排。
        currentConversation = null;
        $("ask-conversation").selectedIndex = 0;
        $("ask-delete-conversation").disabled = true;
        $("ask-query").value = result.question;
        setText($("ask-status"), msg("历史问答 · {date}", {date: dateLabel(item.created_at)}));
        renderAnswer(result);
      }),
      element("p", "", dateLabel(item.created_at)),
    );
    row.append(
      copy,
      action(msg("导出"), () =>
        download(`/api/history/${item.id}/export`, `${item.id}.json`),
      ),
    );
    target.append(row);
  }
}

export function renderEvaluation(report) {
  const card = element("article", "card");
  card.append(element("h2", "", msg("检索对比结果")));
  const first = Object.values(report.results)[0];
  const metrics = Object.keys(first).filter((key) => key !== "details");
  const rows = Object.entries(report.results).map(([name, values]) => [
    strategyLabel(name),
    ...metrics.map((key) =>
      typeof values[key] === "number"
        ? key === "n"
          ? values[key]
          : values[key].toFixed(4)
        : values[key],
    ),
  ]);
  card.append(table([msg("检索策略"), ...metrics], rows));
  card.append(
    element(
      "p",
      "notice",
      msg("{scope}。题目来源：{origin}。", {scope: serviceText(report.metrics_scope), origin: report.dataset_origin}),
    ),
  );
  const actions = element("div", "result-actions");
  actions.append(
    action(msg("导出完整结果"), () =>
      download(`/api/history/${report.id}/export`, `${report.id}.json`),
    ),
    element("span", "muted", msg("索引版本 {revision}", {revision: report.index_revision})),
  );
  card.append(actions);
  $("evaluation-result").replaceChildren(card);
  const details = element("details", "evaluation-details");
  details.append(element("summary", "", msg("逐题结果与失败分析")));
  const ids = new Set(Object.values(report.results).flatMap(value => value.details.map(row => String(row.qid))));
  const methods = Object.keys(report.results);
  for (const id of ids) {
    const rows = methods.map(method => [method, report.results[method].details.find(row => String(row.qid) === id)]);
    const section = element("section", "card");
    section.append(element("h3", "", `${id} · ${rows[0][1]?.question || ""}`));
    section.append(table([msg("策略"), msg("标准出处"), msg("返回出处"), msg("结果"), msg("耗时 ms")], rows.map(([method, row]) => [strategyLabel(method), row?.gold.join("; "), row?.ranked.join("; "), row?.all_hit ? msg("全部覆盖") : row?.hit ? msg("部分覆盖") : msg("未命中"), row?.latency_ms ?? "—"])));
    details.append(section);
  }
  card.append(details);
}

export async function loadEvaluations() {
  const { items } = await request("/api/evaluations");
  const target = $("evaluation-list");
  target.replaceChildren();
  if (!items.length) target.append(element("div", "empty", msg("暂无实验记录")));
  for (const item of items) {
    const row = element("div", "history-item");
    row.append(
      action(item.query, async () => {
        const { result } = await request(`/api/history/${item.id}`);
        renderEvaluation(result);
      }),
      element("span", "muted", dateLabel(item.created_at)),
    );
    target.append(row);
  }
}

let currentConversation = null;

/** 追加一条聊天气泡，返回气泡节点。 */
function appendBubble(role, content) {
  const turn = element("div", `chat-turn ${role}`);
  const bubble = element("div", "chat-bubble", content);
  turn.append(bubble);
  const target = $("answer-result");
  target.append(turn);
  target.scrollTop = target.scrollHeight;
  return bubble;
}

/** 追加一轮助手回答（回答卡＋证据卡片）。 */
function appendTurn(result) {
  const turn = element("div", "chat-turn assistant");
  const bubble = element("div", "chat-bubble");
  turn.append(bubble);
  const target = $("answer-result");
  target.append(turn);
  renderAnswer(result, bubble);
  target.scrollTop = target.scrollHeight;
}

function removeThinking() {
  document
    .querySelectorAll("#answer-result .chat-bubble.thinking")
    .forEach((node) => node.closest(".chat-turn").remove());
}

async function loadConversations() {
  const { items } = await request("/api/conversations");
  const select = $("ask-conversation");
  const placeholder = new Option(t("选择历史对话"), "");
  select.replaceChildren(placeholder);
  for (const item of items) {
    const option = new Option(item.title || item.id, item.id);
    option.selected = item.id === currentConversation;
    select.append(option);
  }
  placeholder.selected = !currentConversation;
  $("ask-delete-conversation").disabled = !currentConversation;
}

/** 首次提问时自动建会话；返回是否新建。 */
async function ensureConversation() {
  if (currentConversation) return false;
  const { conversation } = await request("/api/conversations", {
    method: "POST",
    body: {},
  });
  currentConversation = conversation.id;
  await loadConversations();
  return true;
}

export function initResearch(navigate) {
  loadConversations().catch(() => {});
  $("search-form").addEventListener(
    "submit",
    guarded(async (event) => {
      await busy(event.submitter, async () => {
        setText($("search-status"), msg("正在检索…"));
        const result = await request("/api/search", {
          method: "POST",
          body: {
            query: $("search-query").value,
            strategy: $("search-strategy").value,
            top_k: Number($("search-top-k").value),
            filters: readFilters("search"),
            paper_ids: paperScope(),
          },
        });
        const target = $("search-results");
        target.replaceChildren();
        target.append(scopeDescription(result.scope));
        warnings(target, result.warnings);
        result.hits.forEach((hit, index) =>
          target.append(evidenceCard(hit, index, openDocument)),
        );
        if (!result.hits.length)
          target.append(
            element(
              "div",
              "empty card",
              msg("没有找到相关证据，请调整检索词或补充文献。"),
            ),
          );
        setText($("search-status"), msg("找到 {count} 个片段 · {ms} 毫秒 · 索引版本 {revision}", {count: result.hits.length, ms: result.latency_ms, revision: result.index_revision}));
      });
    }),
  );
  $("ask-form").addEventListener(
    "submit",
    guarded(async (event) => {
      const query = $("ask-query").value;
      const created = await ensureConversation();
      if (created) $("answer-result").replaceChildren();
      $("ask-query").value = "";
      appendBubble("user", query);
      const thinking = appendBubble("assistant", msg("正在思考…"));
      thinking.classList.add("thinking");
      try {
        await busy(event.submitter, async () => {
          const result = await runJob(
            "/api/ask",
            {
              query,
              strategy: $("ask-strategy").value,
              top_k: Number($("ask-top-k").value),
              filters: readFilters("ask"),
              paper_ids: paperScope(),
              context_neighbors: $("ask-expand-context").checked ? 1 : 0,
              conversation_id: currentConversation,
            },
            progress("ask"),
          );
          thinking.closest(".chat-turn").remove();
          appendTurn(result);
          await loadConversations();
          document.dispatchEvent(new Event("biokb:data-changed"));
        });
      } catch (error) {
        removeThinking();
        throw error;
      }
    }),
  );
  $("ask-conversation").addEventListener(
    "change",
    guarded(async (event) => {
      currentConversation = event.currentTarget.value || null;
      $("ask-delete-conversation").disabled = !currentConversation;
      $("answer-result").replaceChildren();
      if (!currentConversation) return;
      const { messages } = await request(
        `/api/conversations/${encodeURIComponent(currentConversation)}`,
      );
      for (const message of messages) {
        if (message.role === "user") appendBubble("user", message.content);
        else if (message.run_id) {
          const { result } = await request(
            `/api/history/${encodeURIComponent(message.run_id)}`,
          );
          appendTurn(result);
        } else appendBubble("assistant", message.content);
      }
    }),
  );
  $("ask-new-conversation").addEventListener(
    "click",
    guarded(async () => {
      currentConversation = null;
      await loadConversations();
      $("answer-result").replaceChildren();
      $("ask-query").focus();
    }),
  );
  $("ask-delete-conversation").addEventListener(
    "click",
    guarded(async () => {
      if (!currentConversation) return;
      await request(`/api/conversations/${encodeURIComponent(currentConversation)}`, {
        method: "DELETE",
      });
      currentConversation = null;
      await loadConversations();
      $("answer-result").replaceChildren();
      document.dispatchEvent(new Event("biokb:data-changed"));
    }),
  );
  $("refresh-history").addEventListener(
    "click",
    guarded(() => loadHistory(navigate)),
  );
  $("evaluation-form").addEventListener(
    "submit",
    guarded(async (event) => {
      await busy(event.submitter, async () => {
        const file = $("evaluation-file").files[0];
        if (file.size > 4 * 1024 * 1024)
          throw new Error("网页评估集不能超过 4 MB");
        let dataset;
        try {
          dataset = JSON.parse(await file.text());
        } catch {
          throw new Error("评估集不是有效 JSON");
        }
        const report = await runJob(
          "/api/evaluations",
          { dataset, k: Number($("evaluation-k").value), name: file.name,
            strategies: $("evaluation-rerank").checked ? ["bm25", "dense", "hybrid", "hybrid_rerank"] : ["bm25", "dense", "hybrid"] },
          progress("evaluation"),
        );
        renderEvaluation(report);
        await loadEvaluations();
      });
    }),
  );
}
