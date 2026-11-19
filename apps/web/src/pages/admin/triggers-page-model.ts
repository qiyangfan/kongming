/* 触发器页的表单状态、类型字典与保存载荷拼装(纯函数,不依赖 React)。 */

import type { StatusMeta } from "@/components/status-badge";
import type { TriggerItem } from "@/lib/trigger-api";

export const PAGE_SIZE = 10;

// 触发器类型中文(列表徽章与表单下拉同源;后端枚举 CRON/WEBHOOK/EMAIL)
export const TYPE_LABELS: Record<string, string> = {
  CRON: "定时调度",
  WEBHOOK: "webhook 触发",
  EMAIL: "邮件触发",
};

export type TriggerType = "CRON" | "WEBHOOK" | "EMAIL";
export type TriggerTargetType = "workflow" | "bot" | "task";

// 执行状态中文(后端枚举:成功/失败/间隔节流跳过/挂起重试);未收录值回退原值
export const RUN_STATUS_META: StatusMeta = {
  SUCCESS: { label: "成功", variant: "secondary" },
  FAILED: { label: "失败", variant: "destructive" },
  SKIPPED: { label: "已跳过", variant: "outline" },
  PENDING: { label: "待重试", variant: "outline" },
};

// 最小间隔快捷档(点击填入对应秒数)
export const INTERVAL_PRESETS: { label: string; seconds: number }[] = [
  { label: "1 分钟", seconds: 60 },
  { label: "5 分钟", seconds: 300 },
  { label: "15 分钟", seconds: 900 },
  { label: "1 小时", seconds: 3600 },
];

export type FormState = {
  id: string | null;
  tgKey: string;
  name: string;
  type: TriggerType;
  targetType: TriggerTargetType;
  targetKey: string;
  // task 目标模板表单(预算/拓扑走 API 配置,不在表单面)
  taskGoal: string;
  taskDatasource: string;
  taskConfirmAuto: boolean;
  taskThreadField: string;
  taskFilterActions: string;
  // 编辑回显的原始模板载体:非表单面字段(预算/拓扑/filter.equals)经它
  // 在保存时原样透传,防止表单保存静默丢弃 API 侧配置
  taskRawTemplate: Record<string, unknown> | null;
  cronExpr: string;
  minInterval: string;
  adapter: "custom" | "github";
  notifyUrl: string;
  secret: string;
  maxRetries: string;
  // 邮件形态(凭证仅写入不回显;编辑空框=保持现值)
  imapHost: string;
  imapPort: string;
  imapTls: boolean;
  imapUser: string;
  imapPassword: string;
  imapFolder: string;
  imapPollInterval: string;
};

export const EMPTY_FORM: FormState = {
  id: null,
  tgKey: "",
  name: "",
  type: "CRON",
  targetType: "workflow",
  targetKey: "",
  taskGoal: "",
  taskDatasource: "",
  taskConfirmAuto: false,
  taskThreadField: "",
  taskFilterActions: "",
  taskRawTemplate: null,
  cronExpr: "0 9 * * *",
  minInterval: "60",
  adapter: "custom",
  notifyUrl: "",
  secret: "",
  maxRetries: "0",
  imapHost: "",
  imapPort: "993",
  imapTls: true,
  imapUser: "",
  imapPassword: "",
  imapFolder: "INBOX",
  imapPollInterval: "300",
};

// 邮件形态保存前校验(创建须填主机/账号/凭证;编辑凭证空框=保持现值)
export function emailFormInvalid(form: FormState): boolean {
  const port = Number(form.imapPort);
  return (
    form.imapHost.trim() === "" ||
    form.imapUser.trim() === "" ||
    (form.id === null && form.imapPassword === "") ||
    !Number.isInteger(port) ||
    port < 1 ||
    port > 65535
  );
}

// 重试上限解析与值域(0–5;空串=未填按 0;非整数/越界为非法)
export function parseMaxRetries(raw: string): { value: number; invalid: boolean } {
  const t = raw.trim();
  if (t === "") return { value: 0, invalid: false };
  const n = Number(t);
  const invalid = !Number.isInteger(n) || n < 0 || n > 5;
  return { value: invalid ? 0 : n, invalid };
}

// 目标模板里的动作过滤(数组)转成逗号分隔输入;缺失时为空串
function filterActionsOf(row: TriggerItem): string {
  // 自由 dict 字段在生成类型中为 {},显式收窄到已知形态
  const flt = row.task_template?.filter as { actions?: unknown } | undefined;
  const actions = flt?.actions;
  return Array.isArray(actions) ? (actions as string[]).join(",") : "";
}

// 目标模板 → 表单里的任务字段(模板缺失时取空值)
function taskTemplateFields(tpl: TriggerItem["task_template"]) {
  return {
    taskGoal: String(tpl?.goal_template ?? ""),
    taskDatasource: String(tpl?.datasource_key ?? ""),
    taskConfirmAuto: tpl?.confirm_auto === true,
    taskThreadField: String(tpl?.thread_field ?? ""),
  };
}

// 列表行 → 编辑表单(模板里非表单面的键暂存到 taskRawTemplate 待保存透传)
export function toFormState(row: TriggerItem): FormState {
  return {
    id: row.id,
    tgKey: row.tg_key,
    name: row.name,
    type: row.type as TriggerType,
    targetType: row.target_type as TriggerTargetType,
    targetKey: row.target_key,
    ...taskTemplateFields(row.task_template),
    taskRawTemplate: (row.task_template as Record<string, unknown> | null) ?? null,
    taskFilterActions: filterActionsOf(row),
    cronExpr: row.cron_expr ?? "0 9 * * *",
    minInterval: String(row.min_interval_sec),
    adapter: row.adapter as "custom" | "github",
    notifyUrl: row.notify_url,
    secret: "",
    maxRetries: String(row.max_retries ?? 0),
    imapHost: row.imap_host ?? "",
    imapPort: String(row.imap_port ?? 993),
    imapTls: row.imap_tls ?? true,
    imapUser: row.imap_user ?? "",
    imapPassword: "", // 空框=保持现值(后端口径)
    imapFolder: row.imap_folder ?? "INBOX",
    imapPollInterval: String(row.imap_poll_interval_sec ?? 300),
  };
}

const TASK_MANAGED_KEYS = [
  "goal_template",
  "datasource_key",
  "confirm_auto",
  "thread_field",
  "filter",
];

// 表单值 → 数据任务目标模板:非表单面字段原样透传,表单管理键按「有值覆盖、清空删除」重建
function buildTaskTemplate(form: FormState): Record<string, unknown> {
  const rawTpl = { ...(form.taskRawTemplate ?? {}) } as Record<string, unknown>;
  const rawFilter = (rawTpl.filter ?? null) as Record<string, unknown> | null;
  for (const k of TASK_MANAGED_KEYS) {
    delete rawTpl[k];
  }
  const taskActions = form.taskFilterActions.trim();
  return {
    ...rawTpl,
    goal_template: form.taskGoal.trim(),
    datasource_key: form.taskDatasource,
    confirm_auto: form.taskConfirmAuto,
    ...(form.taskThreadField.trim() ? { thread_field: form.taskThreadField.trim() } : {}),
    ...(taskActions
      ? {
          filter: {
            ...(rawFilter ?? {}),
            actions: taskActions
              .split(",")
              .map((a) => a.trim())
              .filter(Boolean),
          },
        }
      : {}),
  };
}

// 数据任务目标必须同时填写模板与数据源,否则不发起保存
export function taskFormInvalid(form: FormState): boolean {
  return form.targetType === "task" && (form.taskGoal.trim() === "" || form.taskDatasource === "");
}

// 表单值 → 更新载荷(邮件形态附 IMAP 配置;创建时由调用方再补 tg_key/type/secret)
export function buildUpdatePayload(form: FormState): Record<string, unknown> {
  const retries = parseMaxRetries(form.maxRetries);
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    target_type: form.targetType,
    target_key: form.targetType === "task" ? "" : form.targetKey.trim(),
    ...(form.targetType === "task" ? { task_template: buildTaskTemplate(form) } : {}),
    cron_expr: form.type === "CRON" ? form.cronExpr.trim() : null,
    // 空串才回退 60,0 是合法值,不可用 ||
    min_interval_sec: form.minInterval.trim() === "" ? 60 : Number(form.minInterval),
    adapter: form.adapter,
    notify_url: form.notifyUrl.trim(),
    // 失败重试次数上限(0=不重试)
    max_retries: retries.value,
  };
  if (form.type === "EMAIL") {
    // 邮件形态配置(凭证空框=后端保持现值;下限封顶在后端)
    payload.imap_host = form.imapHost.trim();
    payload.imap_port = Number(form.imapPort);
    payload.imap_tls = form.imapTls;
    payload.imap_user = form.imapUser.trim();
    payload.imap_password = form.imapPassword;
    payload.imap_folder = form.imapFolder.trim() || "INBOX";
    payload.imap_poll_interval_sec =
      form.imapPollInterval.trim() === "" ? 300 : Number(form.imapPollInterval);
  }
  return payload;
}
