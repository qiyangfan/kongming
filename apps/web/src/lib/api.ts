/* API 客户端。类型契约由 ADR-005 管线生成:
   pnpm contracts:gen → api-types.gen.ts(禁手写接口类型)。 */

const BASE = "/api";

let accessToken: string | null = null;

export function setToken(token: string | null) {
  accessToken = token;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init?.headers,
    },
  });
  if (!resp.ok) {
    // 优先取 FastAPI {detail} 文案,原始 JSON 体不直接抛给用户
    let detail = `HTTP ${resp.status}`;
    try {
      const body: unknown = await resp.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : detail;
      }
    } catch {
      /* 非 JSON 响应,保留 HTTP 状态码文案 */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
};
