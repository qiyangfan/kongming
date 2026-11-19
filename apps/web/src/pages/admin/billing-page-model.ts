/* 计费页的数值解析、表单状态与写操作载荷拼装(纯函数,不依赖 React)。 */

import type { StatusMeta } from "@/components/status-badge";
import { createPlan, upsertPolicy, updatePlan, type PlanItem } from "@/lib/billing-api";

export const INVOICE_STATUS_META: StatusMeta = {
  PENDING: { label: "待支付", variant: "secondary" },
  PAID: { label: "已支付", variant: "secondary" },
  VOID: { label: "已作废", variant: "secondary" },
};

/** 限制金额字段，避免超出数据库 INTEGER 范围后落库失败。 */
const INT32_MAX = 2_147_483_647;

/** 数值仅接受非负整数；非法输入返回 null，由表单显示错误。 */
function parseNonNegInt(raw: string): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const n = Number(raw);
  return Number.isSafeInteger(n) && n <= INT32_MAX ? n : null;
}

/** 套餐数值行内校验错误文案(创建/编辑共用) */
const PLAN_NUM_ERROR = "额度须为 0 到 21 亿之间的整数;单价须为非负金额,最多 3 位小数";

/** 单价以「元」录入(精度 0.001 元=1 厘),提交转整数厘;亚厘四舍五入 */
function parseNonNegYuanLi(raw: string): number | null {
  const t = raw.trim();
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const n = Math.round(Number(t) * 1000);
  return Number.isSafeInteger(n) && n <= INT32_MAX ? n : null;
}

export type PlanForm = {
  plan_key: string;
  name: string;
  monthly_tokens: string;
  monthly_calls: string;
  price_per_mtok: string;
  price_per_call: string;
};

export const EMPTY_PLAN_FORM: PlanForm = {
  plan_key: "",
  name: "",
  monthly_tokens: "0",
  monthly_calls: "0",
  price_per_mtok: "0",
  price_per_call: "0",
};

export type PolicyForm = {
  dimension: string;
  limit_value: string;
  warn_pct: string;
  scope: string;
  scope_ref: string;
};

export const EMPTY_POLICY_FORM: PolicyForm = {
  dimension: "TOKENS",
  limit_value: "0",
  warn_pct: "80",
  scope: "TENANT",
  scope_ref: "",
};

type PlanNumbers = {
  monthly_tokens: number;
  monthly_calls: number;
  price_per_mtok: number;
  price_per_call: number;
};

// 套餐四项数值统一解析;任一非法即返回 null(调用方抛统一校验错误)
function parsePlanNumbers(form: PlanForm): PlanNumbers | null {
  const nums = {
    monthly_tokens: parseNonNegInt(form.monthly_tokens),
    monthly_calls: parseNonNegInt(form.monthly_calls),
    price_per_mtok: parseNonNegYuanLi(form.price_per_mtok),
    price_per_call: parseNonNegYuanLi(form.price_per_call),
  };
  const values = Object.values(nums);
  if (values.some((n) => n === null)) return null;
  // Object.values 丢元组字面量类型,显式解构以保留生成请求类型对字段的检查
  const [monthly_tokens, monthly_calls, price_per_mtok, price_per_call] = values as [
    number,
    number,
    number,
    number,
  ];
  return { monthly_tokens, monthly_calls, price_per_mtok, price_per_call };
}

export function createPlanFromForm(form: PlanForm) {
  const nums = parsePlanNumbers(form);
  if (!nums) throw new Error(PLAN_NUM_ERROR);
  return createPlan({ plan_key: form.plan_key.trim(), name: form.name.trim(), ...nums });
}

export function updatePlanFromForm(plan: PlanItem, form: PlanForm) {
  const nums = parsePlanNumbers(form);
  if (!nums) throw new Error(PLAN_NUM_ERROR);
  return updatePlan(plan.id, { name: form.name.trim(), ...nums });
}

// 策略上限按维度解析:成本按元换算厘,用量按整数
export function upsertPolicyFromForm(tenantId: string, form: PolicyForm) {
  const limit =
    form.dimension === "COST"
      ? parseNonNegYuanLi(form.limit_value)
      : parseNonNegInt(form.limit_value);
  const warn = parseNonNegInt(form.warn_pct);
  if (limit === null || warn === null) {
    throw new Error(
      form.dimension === "COST"
        ? "上限须为非负金额(最多 3 位小数);预警须为 0 到 21 亿的整数"
        : "上限/预警须为 0 到 21 亿的整数",
    );
  }
  return upsertPolicy({
    tenant_id: tenantId,
    dimension: form.dimension,
    limit_value: limit,
    warn_pct: warn,
    scope: form.scope,
    scope_ref: form.scope === "TENANT" ? null : form.scope_ref || null,
  });
}
