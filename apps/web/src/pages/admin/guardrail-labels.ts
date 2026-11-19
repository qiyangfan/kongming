/* 护栏的层与动作中文映射(规则表与触发审计共用)。 */

import type { StatusMeta } from "@/components/status-badge";

export const TYPE_LABELS: Record<string, string> = {
  input: "输入",
  output: "输出",
  tool_intent: "工具意图",
};

export const ACTION_META: StatusMeta = {
  blocked: { label: "拦截", variant: "secondary" },
  masked: { label: "脱敏", variant: "default" },
};
