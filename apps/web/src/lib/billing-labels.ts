// 计费域枚举的中文展示文案(门户 PortalPage 与管理端 BillingPage 共用,单一真理源)。
// 取值回退原样展示:后端新增枚举值时前端不白屏,以原文暴露异常源。
export const DIM_TEXT: Record<string, string> = {
  TOKENS: "Token 用量",
  CALLS: "调用次数",
  COST: "成本(元)",
};

// 配额策略 scope 展示文案(下拉/列表徽标共用)
export const SCOPE_TEXT: Record<string, string> = {
  TENANT: "租户",
  BOT: "助手",
  USER: "用户",
  TEAM: "团队",
};

export const INVOICE_STATUS_TEXT: Record<string, string> = {
  PENDING: "待支付",
  PAID: "已支付",
  VOID: "已作废",
};

export const PAYMENT_STATUS_TEXT: Record<string, string> = {
  CREATED: "支付中",
  PAID: "已支付",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
};
