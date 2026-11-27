/* MCP 页的表单状态与请求体装配:新建忽略空值行,编辑按「空值保持、删除行清键」语义。 */

import type { McpServerOut } from "@kongming/api-client/mcp-api";

export type HeaderRow = { key: string; value: string };

export type FormState = {
  id: string | null; // null=新建
  serverKey: string;
  name: string;
  endpoint: string;
  headers: HeaderRow[];
  removedKeys: string[]; // 编辑态删除的头键(提交时空串=服务端删除该键)
  existingKeys: string[]; // 编辑态已有键；键名锁定，避免改名后产生重复配置。
};

export const EMPTY_FORM: FormState = {
  id: null,
  serverKey: "",
  name: "",
  endpoint: "",
  headers: [],
  removedKeys: [],
  existingKeys: [],
};

/** 新建:空值行忽略(无"保持现值"语义)。 */
export function createPayload(form: FormState) {
  return {
    server_key: form.serverKey.trim(),
    name: form.name.trim(),
    endpoint: form.endpoint.trim(),
    headers: Object.fromEntries(
      form.headers
        .filter((h) => h.key.trim() && h.value !== "")
        .map((h) => [h.key.trim(), h.value]),
    ),
  };
}

/** 编辑:值留空=保持现值(null);删除的行=空串(服务端删除该键)。 */
export function updatePayload(form: FormState) {
  const headers: Record<string, string | null> = {};
  for (const k of form.removedKeys) headers[k] = ""; // 先删
  for (const h of form.headers) {
    if (h.key.trim()) headers[h.key.trim()] = h.value === "" ? null : h.value;
  }
  return { name: form.name.trim(), endpoint: form.endpoint.trim(), headers };
}

/** 列表行 → 编辑表单:已有键名锁定,值留空。 */
export function formFromRow(row: McpServerOut): FormState {
  return {
    id: row.id,
    serverKey: row.server_key,
    name: row.name,
    endpoint: row.endpoint,
    headers: row.header_keys.map((k) => ({ key: k, value: "" })),
    removedKeys: [],
    existingKeys: [...row.header_keys],
  };
}

/** 测试/同步的可读结果文案;失败带分类名与详情。 */
export function actionNote(
  kind: "test" | "sync",
  r: {
    ok: boolean;
    kind?: string | null;
    detail?: string | null;
    tools?: number | null;
    resources?: number | null;
    prompts?: number | null;
  },
): string {
  const counts = `工具 ${r.tools ?? 0} / 资源 ${r.resources ?? 0} / 模板 ${r.prompts ?? 0}`;
  if (kind === "test") return r.ok ? `连通正常:${counts}` : `连通失败(${r.kind}):${r.detail}`;
  return r.ok ? `同步完成:注册${counts}` : `同步失败(${r.kind}):${r.detail}`;
}

/** 替换指定下标的请求头行,返回新的行数组。 */
export function replaceHeader(
  rows: HeaderRow[],
  index: number,
  patch: Partial<HeaderRow>,
): HeaderRow[] {
  return rows.map((x, j) => (j === index ? { ...x, ...patch } : x));
}
