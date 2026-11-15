/** HTTP 与异步任务通信；页面不保存模型密钥。 */
export async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = sessionStorage.getItem("biokb_access_token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = options.body;
  if (body && !(body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);
  try {
    const response = await fetch(path, {
      ...options,
      headers,
      body,
      signal: controller.signal,
    });
    const data = await response.json();
    if (response.status === 401)
      document.dispatchEvent(new Event("biokb:unauthorized"));
    if (!response.ok || data.success === false) {
      const error = new Error(data.error || "请求失败");
      error.code = data.code;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请稍后重试");
    if (error instanceof TypeError) throw new Error("无法连接服务，请检查网络或服务状态");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export async function runJob(path, body, onProgress) {
  let { job } = await request(path, { method: "POST", body });
  const started = Date.now();
  while (true) {
    onProgress?.(job);
    if (job.status === "completed") return job.result;
    if (["failed", "interrupted"].includes(job.status)) throw new Error(job.error || "任务处理失败");
    if (Date.now() - started > 20 * 60 * 1000)
      throw new Error(
        "页面等待已结束，任务可能仍在后台运行，请查看问答记录或文献列表",
      );
    await new Promise((resolve) => setTimeout(resolve, 700));
    ({ job } = await request(`/api/jobs/${encodeURIComponent(job.id)}`));
  }
}

export async function download(path, filename) {
  const headers = {};
  const token = sessionStorage.getItem("biokb_access_token");
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { headers });
  if (!response.ok) throw new Error("下载失败，请检查访问权限或文件是否存在");
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}
