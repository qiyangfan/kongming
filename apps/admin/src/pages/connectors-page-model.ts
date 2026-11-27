/* 连接器页的类型字典、表单状态与「表单 ↔ 请求体」的纯函数装配(可测逻辑)。 */

import type { ConnectorOut, ConnectorParamIn } from "@kongming/api-client/connector-api";
import { cellText } from "@kongming/api-client/utils";

export const METHOD_LABELS: Record<string, string> = { GET: "GET(读)", POST: "POST(写)" };
export const AUTH_LABELS: Record<string, string> = {
  none: "无鉴权",
  bearer: "Bearer 令牌",
  api_key: "API-Key 头",
  basic: "Basic(user:password)",
};
export const PARAM_TYPE_LABELS: Record<string, string> = {
  string: "字符串",
  number: "数值",
  boolean: "布尔",
};

export type ParamRow = ConnectorParamIn;

export type FormState = {
  id: string | null; // null=新建
  key: string;
  name: string;
  description: string;
  method: "GET" | "POST";
  pathTemplate: string;
  params: ParamRow[];
  allowedHostsText: string; // 一行一个主机
  authType: string;
  authSecret: string; // 编辑态留空=不修改(空框语义)
  confirmMode: "auto" | "yes" | "no";
  timeoutMs: string;
};

export function emptyForm(): FormState {
  return {
    id: null,
    key: "",
    name: "",
    description: "",
    method: "GET",
    pathTemplate: "",
    params: [],
    allowedHostsText: "",
    authType: "none",
    authSecret: "",
    confirmMode: "auto",
    timeoutMs: "5000",
  };
}

function parseHosts(text: string): string[] {
  return text
    .split("\n")
    .map((h) => h.trim())
    .filter((h) => h.length > 0);
}

function resolveConfirmation(mode: "auto" | "yes" | "no"): boolean | null {
  if (mode === "auto") return null; // 缺省规则:非 GET 需确认(GET 豁免)
  return mode === "yes";
}

export function formToBody(form: FormState): {
  key: string;
  name: string;
  description: string;
  method: string;
  path_template: string;
  params: ConnectorParamIn[];
  allowed_hosts: string[];
  auth_type: string;
  auth_secret: string | null;
  requires_confirmation: boolean | null;
  timeout_ms: number;
} {
  const secret = form.authSecret.trim();
  return {
    key: form.key.trim(),
    name: form.name.trim(),
    description: form.description.trim(),
    method: form.method,
    path_template: form.pathTemplate.trim(),
    params: form.params.map((p) => ({
      name: p.name.trim(),
      type: p.type,
      required: p.required,
      description: p.description.trim(),
    })),
    allowed_hosts: parseHosts(form.allowedHostsText),
    auth_type: form.authType,
    // 空框语义:留空=不修改(服务端 null 语义);填入=替换
    auth_secret: secret === "" ? null : secret,
    requires_confirmation: resolveConfirmation(form.confirmMode),
    timeout_ms: Number(form.timeoutMs) || 0,
  };
}

export function rowToForm(row: ConnectorOut): FormState {
  return {
    id: row.id,
    key: row.key,
    name: row.name,
    description: row.description,
    method: row.method === "POST" ? "POST" : "GET",
    pathTemplate: row.path_template,
    params: row.params.map((p) => ({
      name: cellText(p.name),
      type: cellText(p.type) || "string",
      required: Boolean(p.required),
      description: cellText(p.description),
    })),
    allowedHostsText: row.allowed_hosts.join("\n"),
    authType: row.auth_type,
    authSecret: "", // 凭证不回填(空框语义)
    confirmMode: row.requires_confirmation ? "yes" : "no",
    timeoutMs: String(row.timeout_ms),
  };
}

// 测试调用对话框的声明快照(行级或表单级入口共用)
export type TestSnapshot = {
  title: string;
  body: ReturnType<typeof formToBody> & { use_saved_secret: boolean };
  argNames: { name: string; type: string; required: boolean }[];
};
