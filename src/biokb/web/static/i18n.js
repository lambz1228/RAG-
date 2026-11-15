/** 显式标记界面文案；文献、问题和模型正文不参与翻译。 */
import { english } from "./messages.js";

let locale = "zh-CN";
try { locale = localStorage.getItem("biokb_locale") === "en" ? "en" : "zh-CN"; } catch { /* 禁用存储时仍可切换当前页面。 */ }
export const getLocale = () => locale;
export const msg = (key, params = {}) => ({ i18n: key, params });
export const dateText = (value) => msg("@date", { value });

export function translate(value) {
  if (!value || typeof value !== "object" || !value.i18n) return String(value ?? "");
  const { i18n: key, params } = value;
  if (key === "@date") return new Date(params.value).toLocaleString(locale === "en" ? "en-GB" : "zh-CN", { hour12: false });
  const template = locale === "en" ? (english[key] ?? key) : key;
  return template.replace(/\{(\w+)\}/g, (_, name) => translate(params[name] ?? `{${name}}`));
}
export const t = (key, params = {}) => translate(msg(key, params));

export function setText(node, value) {
  if (value?.i18n) {
    node.dataset.i18n = value.i18n;
    node.dataset.i18nParams = JSON.stringify(value.params);
  } else {
    delete node.dataset.i18n;
    delete node.dataset.i18nParams;
  }
  node.textContent = translate(value);
  return node;
}

export function applyTranslations(root = document) {
  root.querySelectorAll("[data-i18n]").forEach(node => {
    node.textContent = t(node.dataset.i18n, JSON.parse(node.dataset.i18nParams || "{}"));
  });
  for (const attribute of ["placeholder", "title", "aria-label"]) {
    root.querySelectorAll(`[data-i18n-${attribute}]`).forEach(node => {
      node.setAttribute(attribute, t(node.getAttribute(`data-i18n-${attribute}`)));
    });
  }
}

export function setLocale(value) {
  locale = value === "en" ? "en" : "zh-CN";
  try { localStorage.setItem("biokb_locale", locale); } catch { /* 浏览器存储不可用时不阻断交互。 */ }
  document.documentElement.lang = locale;
  applyTranslations();
  document.querySelectorAll(".language-select").forEach(select => { select.value = locale; });
  document.dispatchEvent(new Event("biokb:locale-changed"));
}

/** 仅在服务状态、定位信息和错误出口使用；不处理研究内容。 */
export function serviceText(value) {
  const text = String(value || "");
  if (Object.hasOwn(english, text)) return msg(text);
  for (const [pattern, key, names] of [
    [/^字符 (\d+)–(\d+)$/, "字符 {start}–{end}", ["start", "end"]],
    [/^第 (\d+)–(\d+) 页 · (.+)$/, "第 {start}–{end} 页 · {position}", ["start", "end", "position"]],
    [/^正文块 (\d+) · (.+)$/, "正文块 {number} · {position}", ["number", "position"]],
    [/^数值参数必须为 0–(\d+) 的整数$/, "数值参数必须为 0–{maximum} 的整数", ["maximum"]],
    [/^评估题 (\d+) 需要 question 字符串$/, "评估题 {number} 需要 question 字符串", ["number"]],
    [/^评估题 (\d+) 缺少(有效的)?标准出处$/, "评估题 {number} 缺少标准出处", ["number"]],
    [/^模型服务返回 HTTP (\d+)，请检查服务、模型和凭据配置$/, "模型服务返回 HTTP {status}，请检查服务、模型和凭据配置", ["status"]],
    [/^评估集中有 (\d+) 个标准出处不在知识库中，请导入对应文献或提供经核验的 source_map；首个缺失编号：(.+)$/, "评估集中有 {count} 个标准出处不在知识库中，请导入对应文献或提供经核验的 source_map；首个缺失编号：{source}", ["count", "source"]],
  ]) {
    const match = pattern.exec(text);
    if (match) return msg(key, Object.fromEntries(names.map((name, i) => [name, name === "position" ? serviceText(match[i + 1]) : match[i + 1]])));
  }
  return text;
}

export function initI18n() {
  document.querySelectorAll(".language-select").forEach(select => {
    select.addEventListener("change", () => setLocale(select.value));
  });
  setLocale(locale);
}
