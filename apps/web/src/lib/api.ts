/* 使用 ADR-005 生成类型；access token 仅存内存，refresh 走 HttpOnly cookie，启动时单飞刷新。 */

import type { components } from "./api-types.gen";
import { TENANT_STORAGE_KEY } from "./storage-keys";

export type TokenPairOut = components["schemas"]["TokenPairOut"];
export type MeOut = components["schemas"]["MeOut"];
export type OptionsOut = components["schemas"]["OptionsOut"];
export type StartOut = components["schemas"]["StartOut"];
export type SessionItemOut = components["schemas"]["SessionItemOut"];
export type SessionMessageOut = components["schemas"]["SessionMessageOut"];
export type ChatQueryOut = components["schemas"]["ChatQueryOut"];
export type TurnCancelOut = components["schemas"]["TurnCancelOut"];
export type TenantRefOut = components["schemas"]["TenantRefOut"];
/* 流式事件 data 形态(契约见 openapi responses oneOf) */
export type StreamStageOut = components["schemas"]["StreamStageOut"];
type StreamSqlOut = components["schemas"]["StreamSqlOut"];
type StreamSummaryOut = components["schemas"]["StreamSummaryOut"];
type StreamErrorOut = components["schemas"]["StreamErrorOut"];
/* tool-calling:tool_call 事件 + 待确认调用 + 确认回执 */
export type StreamToolCallOut = components["schemas"]["StreamToolCallOut"];
export type PendingToolCallOut = components["schemas"]["PendingToolCallOut"];
export type ToolConfirmOut = components["schemas"]["ToolConfirmOut"];

const BASE = "/api";

/* 租户头单点注入(直读持久化键,免逐调用遗漏);对 chat 系端点与数据源 options 有语义,多数管理端点无语义 */
function tenantHeader(): Record<string, string> {
  const t = localStorage.getItem(TENANT_STORAGE_KEY);
  return t ? { "X-Tenant-Id": t } : {};
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
const sessionClearedListeners = new Set<() => void>();

export function onSessionCleared(cb: () => void): () => void {
  sessionClearedListeners.add(cb);
  return () => sessionClearedListeners.delete(cb);
}

export function setTokens(access: string): void {
  accessToken = access;
}

export function clearTokens(): void {
  const had = accessToken !== null;
  accessToken = null;
  if (had) sessionClearedListeners.forEach((cb) => cb());
}

/* 提交刷新请求;2xx 时落盘新令牌返回 true,非 2xx 清空本地态 */
async function postRefresh(): Promise<boolean> {
  const resp = await fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  if (!resp.ok) {
    clearTokens();
    return false;
  }
  const pair = (await resp.json()) as TokenPairOut;
  setTokens(pair.access_token);
  return true;
}

/* 发起一次刷新请求并处理失败清理；网络异常时保留本地态,由下次请求再试 */
async function requestTokenRefresh(): Promise<boolean> {
  try {
    return await postRefresh();
  } catch {
    return false;
  }
}

/* 单飞刷新:并发 401 只触发一次 /auth/refresh */
export function tryRefresh(): Promise<boolean> {
  refreshing ??= requestTokenRefresh().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function doFetch(path: string, init?: RequestInit): Promise<Response> {
  // multipart 不设 Content-Type:boundary 须由浏览器生成,手设会致服务端解析不出表单字段;JSON 显式声明
  const isForm = init?.body instanceof FormData;
  try {
    return await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        ...(isForm ? {} : { "Content-Type": "application/json" }),
        ...tenantHeader(),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...init?.headers,
      },
    });
  } catch (err) {
    // 主动取消(流式 abort)原样透传,不冒充网络错误
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    // fetch reject 的 TypeError 文案是浏览器英文("Failed to fetch" 等),不示人
    const e = new Error("网络连接失败,请检查网络后重试") as Error & { raw?: string };
    e.raw = err instanceof Error ? err.message : String(err);
    throw e;
  }
}

/* ============ 错误文案转译(人类可读单源) ============ */

/** HTTP 状态 → 用户文案(detail 不可用/英文技术文案时的兜底) */
const HTTP_MESSAGES: Record<number, string> = {
  400: "请求有误,请检查后重试",
  401: "登录已过期,请重新登录",
  403: "没有执行此操作的权限",
  404: "目标不存在或已被删除",
  409: "内容有冲突,请刷新后重试",
  422: "提交的内容未通过校验,请检查后重试",
  429: "操作过于频繁,请稍后再试",
  500: "服务开小差了,请稍后重试",
  502: "服务暂不可用,请稍后重试",
  503: "服务维护中,请稍后重试",
  504: "服务响应超时,请稍后重试",
};

/** 后端业务错误码 → 用户文案(语义确定的才收录,未收录码原样保留);码全集以 apps/api 各 router 的 detail 值为唯一事实源,禁前端臆造 */
const ERROR_CODE_LABELS: Record<string, string> = {
  SCHEMA_NOT_ALLOWED: "当前工作空间没有该数据表的访问权限,请联系管理员在数据源中授权",
  TARGET_NOT_FOUND: "目标不存在(可能已删除),请重新选择",
  TRIGGER_KEY_TAKEN: "该触发器标识已被占用,请换一个",
  WORKFLOW_CONFLICT: "工作流已被其他操作更新,请刷新后重试",
  WORKFLOW_DISABLED: "该工作流已停用,请先启用后再运行",
  TENANT_FORBIDDEN: "没有此工作空间的访问权限,请切换工作空间或联系管理员",
  DATASOURCE_INVALID: "数据源不可用或无权访问,请切换后重试",
  BOT_NOT_FOUND: "该助手不存在或未公开",
  SESSION_NOT_FOUND: "该会话不存在或已被删除",
  TEAM_OWNER_REQUIRED: "仅团队所有者可执行此操作",
  TEAM_ROLE_FORBIDDEN: "当前团队角色无权执行此操作",
  EMPTY_QUESTION: "问题不能为空",
  TITLE_INVALID: "会话标题不合法,请调整后重试",
  SENSITIVE_CONTENT: "内容包含敏感信息,请调整后重试",
  MEMORY_SENSITIVE: "记忆内容包含敏感信息,请调整后重试",
  UNSUPPORTED_FILE_TYPE: "不支持的文件类型",
  DOCUMENT_TOO_LARGE: "文档过大",
  ATTACHMENT_TOO_LARGE: "附件过大",
  ATTACHMENT_EMPTY: "附件内容为空",
  ATTACHMENT_TYPE_INVALID: "附件类型不支持",
  // 问答成本控制类(速率/配额)
  CHAT_RATE_LIMITED: "提问太频繁了,请稍后再试",
  CHAT_QUOTA_EXCEEDED: "已达到当前套餐的问答配额上限,请升级套餐或联系管理员",
};

/** 错误码形态:大写字母开头,仅大写字母/数字/下划线,≥3 位 */
const ERROR_CODE_RE = /^[A-Z][A-Z0-9_]{2,}$/;
/** CJK 字符:后端中文自然语直接示人 */
const CJK_RE = /[\u4e00-\u9fff]/;
/** 浏览器网络异常文案(各浏览器措辞不一,兜中文) */
const NETWORK_ERR_RE = /failed to fetch|load failed|network ?error|internet connection/i;

/** 带状态码的 detail → 用户文案(错误码查表;中文原样;英文技术文案以状态码兜底) */
function detailForUser(status: number, detail: string | null): string {
  if (detail == null || detail === "") return HTTP_MESSAGES[status] ?? "请求失败,请稍后重试";
  if (ERROR_CODE_RE.test(detail)) return ERROR_CODE_LABELS[detail] ?? detail;
  if (CJK_RE.test(detail)) return detail;
  return HTTP_MESSAGES[status] ?? "请求失败,请稍后重试";
}

/** 已落地错误文本 → 用户文案(SSE 事件等不经 toStatusError 的通道复用);"HTTP 500" 与错误码形态转中文,其余原样(幂等) */
export function humanizeErrorText(s: string | null | undefined): string {
  if (s == null || s === "") return "请求失败,请稍后重试";
  const http = /^HTTP (\d{3})$/.exec(s);
  if (http) return HTTP_MESSAGES[Number(http[1])] ?? "请求失败,请稍后重试";
  if (NETWORK_ERR_RE.test(s)) return "网络连接失败,请检查网络后重试";
  if (ERROR_CODE_RE.test(s)) return ERROR_CODE_LABELS[s] ?? s;
  if (CJK_RE.test(s)) return s;
  return s;
}

async function toStatusError(
  resp: Response,
): Promise<Error & { status?: number; raw?: string | undefined }> {
  // 优先取 FastAPI {detail} 文案,原始 JSON 体不直接抛给用户;raw 留档供技术详情
  let detail: string | null = null;
  try {
    const body: unknown = await resp.json();
    if (body && typeof body === "object" && "detail" in body) {
      const d = body.detail;
      if (typeof d === "string") detail = d;
    }
  } catch {
    /* 非 JSON 响应,走状态码兜底文案 */
  }
  const err = new Error(detailForUser(resp.status, detail)) as Error & {
    status?: number;
    raw?: string | undefined;
  };
  err.status = resp.status;
  err.raw = detail ?? undefined;
  return err;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 登录/刷新自身的 401 不重放(避免递归)
  const replayable = path !== "/auth/login" && path !== "/auth/refresh";
  let resp = await doFetch(path, init);
  if (resp.status === 401 && replayable && (await tryRefresh())) {
    resp = await doFetch(path, init);
  }
  if (!resp.ok) throw await toStatusError(resp);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** 文件下载类请求(导出):401 重放与错误转译同 request;返回原始 Response,调用方取 blob 前须先辨 JSON 错误形态 */
async function postBlob(path: string, body: unknown): Promise<Response> {
  const init = { method: "POST", body: JSON.stringify(body) };
  let resp = await doFetch(path, init);
  if (resp.status === 401 && (await tryRefresh())) {
    resp = await doFetch(path, init);
  }
  if (!resp.ok) throw await toStatusError(resp);
  return resp;
}

/** GET 文件下载类请求(报告导出):错误语义与 postBlob 同构 */
async function getBlob(path: string): Promise<Response> {
  let resp = await doFetch(path);
  if (resp.status === 401 && (await tryRefresh())) {
    resp = await doFetch(path);
  }
  if (!resp.ok) throw await toStatusError(resp);
  return resp;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  /** multipart 上传(知识库文档);boundary 由浏览器生成,勿手写 Content-Type */
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  postBlob,
  getBlob,
};

/* ============ 流式问答(query-streaming) ============ */

interface StreamHandlers {
  onStage?: (d: StreamStageOut) => void;
  onSql?: (d: StreamSqlOut) => void;
  onSummary?: (d: StreamSummaryOut) => void;
  onToolCall?: (d: StreamToolCallOut) => void;
  onResult?: (d: ChatQueryOut) => void;
  onError?: (d: StreamErrorOut) => void;
  /* 响应头到达时回调本轮标识(X-Trace-Id);之后的停止以它向控制面发起取消 */
  onTraceId?: (traceId: string | null) => void;
}

/* 帧块内逐行抽取 event 与 data 行；出现心跳注释行即返回 null（整帧忽略）。 */
function parseSseLines(block: string): { event: string; data: string } | null {
  let event = "";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) return null; // 心跳注释行
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (!event || dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/* 事件 → 回调与是否终态；未知事件不在表中（前向兼容，直接忽略）。 */
const SSE_EVENT_HANDLERS: Record<
  string,
  { deliver: (h: StreamHandlers, d: unknown) => void; terminal: boolean }
> = {
  stage: { deliver: (h, d) => h.onStage?.(d as StreamStageOut), terminal: false },
  sql: { deliver: (h, d) => h.onSql?.(d as StreamSqlOut), terminal: false },
  summary: { deliver: (h, d) => h.onSummary?.(d as StreamSummaryOut), terminal: false },
  tool_call: { deliver: (h, d) => h.onToolCall?.(d as StreamToolCallOut), terminal: false },
  result: { deliver: (h, d) => h.onResult?.(d as ChatQueryOut), terminal: true },
  error: { deliver: (h, d) => h.onError?.(d as StreamErrorOut), terminal: true },
};

/**
 * 解析一个 SSE 帧块(event:/data: 行,注释行忽略)并分发;畸形 data 静默跳帧。
 * @public 仅供 vitest 断言分帧边界与跳帧
 */
export function dispatchSseBlock(block: string, handlers: StreamHandlers): boolean {
  const frame = parseSseLines(block);
  if (!frame) return false;
  let data: unknown;
  try {
    data = JSON.parse(frame.data);
  } catch {
    return false; // 畸形帧跳过,流不断
  }
  const entry = SSE_EVENT_HANDLERS[frame.event];
  if (!entry) return false;
  entry.deliver(handlers, data);
  return entry.terminal;
}

/* 按 SSE 帧边界切分缓冲区,返回剩余缓冲与是否出现过终态帧 */
function drainSseBuffer(
  buffer: string,
  handlers: StreamHandlers,
): { buffer: string; terminated: boolean } {
  let terminated = false;
  // SSE 帧边界:空行
  for (let idx = buffer.indexOf("\n\n"); idx >= 0; idx = buffer.indexOf("\n\n")) {
    const block = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    if (dispatchSseBlock(block, handlers)) terminated = true;
  }
  return { buffer, terminated };
}

/* 读到流结束,逐块切帧分发;返回是否出现过终态帧 */
async function readSseStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  handlers: StreamHandlers,
): Promise<boolean> {
  const decoder = new TextDecoder();
  let buffer = "";
  let terminated = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    // SSE 规范行尾 CRLF/LF 皆合法:剥除 CR 消除跨 chunk 的 \r\n 分裂边界
    buffer += decoder.decode(value, { stream: true }).replace(/\r/g, "");
    ({ buffer, terminated } = drainSseBuffer(buffer, handlers));
    if (terminated) break;
  }
  return terminated;
}

/* 带 401 单飞刷新重试的 POST 请求 */
async function postWithRefresh(path: string, init: RequestInit): Promise<Response> {
  const resp = await doFetch(path, init);
  if (resp.status === 401 && (await tryRefresh())) return doFetch(path, init);
  return resp;
}

/* 读流并分发帧;主动取消时静默返回(迟到事件由守卫丢弃),其余异常转人类文案 */
async function drainSseStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<boolean> {
  try {
    return await readSseStream(reader, handlers);
  } catch (err) {
    if (signal.aborted) return false;
    throw err instanceof Error ? err : new Error("连接中断");
  } finally {
    void reader.cancel().catch(() => undefined);
  }
}

/** 发起流式问答请求并以 ReadableStream 解析 SSE;HTTP 错误抛给调用方 */
export async function streamChatQuery(
  body: unknown,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const init: RequestInit = { method: "POST", body: JSON.stringify(body), signal };
  const resp = await postWithRefresh("/chat/query/stream", init);
  if (!resp.ok) throw await toStatusError(resp);
  // 先于读流回调:响应头一到即下发本轮标识,停止入口据此决定能否发起取消
  handlers.onTraceId?.(resp.headers.get("X-Trace-Id"));
  const reader = resp.body?.getReader();
  if (!reader) throw new Error("流式响应不可用");
  const terminated = await drainSseStream(reader, handlers, signal);
  // 主动取消发生在 read 之间时 drainSseStream 正常返回,此处靠 abort 状态跳过未终态的报错
  if (!terminated && !signal.aborted) throw new Error("连接中断,答案未完整返回");
}
