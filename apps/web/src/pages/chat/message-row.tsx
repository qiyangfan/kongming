/* 历史消息行与终态快照构造：用户气泡带附件徽标，助手消息交给问答卡片。 */

import { memo } from "react";
import { Paperclip } from "lucide-react";

import { AssistantCard } from "@/components/assistant-card";
import { ChatBubble } from "@/components/chat-bubble";
import type { ChatQueryOut, SessionMessageOut } from "@/lib/api";
import { cellText } from "@/lib/utils";

/** 附件徽标(user 气泡两处共用:历史回显与即时快照) */
function AttachmentBadge({ items }: { items: { [key: string]: unknown }[] }) {
  if (items.length === 0) return null;
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground">
      <Paperclip className="size-3" aria-hidden />
      {items.length} 张图片:
      {items.map((a) => cellText(a.filename)).join(", ")}
    </span>
  );
}

/* 终态快照的状态码：失败按 verdict 区分安全护栏拦截与知识未覆盖。 */
function snapshotStatus(out: ChatQueryOut): SessionMessageOut["status"] {
  if (out.success) return "SUCCESS";
  if (out.verdict === "BLOCKED") return "REJECTED";
  if (out.verdict === "NO_DATA") return "NO_DATA";
  return "FAILED";
}

export function toSyntheticMessage(out: ChatQueryOut): SessionMessageOut {
  return {
    // message_id 缺失(落库容错分支)置空串:FeedbackActions 的 !msg.id 守卫
    // 与 EXPLAIN 入口({msg.sql && ...}同上)天然不渲染,避免非 UUID 直传 422
    id: out.message_id ?? "",
    role: "ASSISTANT",
    question: null,
    summary: out.summary || null,
    sql: out.sql || null,
    status: snapshotStatus(out),
    error: out.error,
    error_detail: out.error_detail ?? null, // 快照与历史回显同源
    result: {
      columns: out.columns,
      rows: out.rows,
      insights: out.insights,
      stats: out.stats,
      chart: out.chart,
      // 终态快照须带候选:漏带时快照期回落单图绑定,历史载入后又切到
      // 候选绑定,产生一次视觉跳变
      chart_candidates: out.chart_candidates ?? [],
      error: out.error,
      error_detail: out.error_detail ?? null, // 同源
      // 终态快照携带产物卡(与历史回显同形);缺档空数组回落既有形态
      artifacts: out.artifacts ?? [],
      // 快照和历史消息使用相同列标签,避免表头在两种状态间切换名称
      column_labels: out.column_labels ?? [],
    },
    rating: null,
    corrected_sql: null,
    feedback_comment: null,
    attachments: out.attachments ?? [], // 终态快照同源
  };
}

/* 用户提问气泡（含附件徽标）。 */
export function UserTurn({
  question,
  attachments,
}: {
  question: string | null | undefined;
  attachments?: { [key: string]: unknown }[];
}) {
  return (
    <div className="flex flex-col items-end gap-1">
      <ChatBubble variant="user">{question}</ChatBubble>
      <AttachmentBadge items={attachments ?? []} />
    </div>
  );
}

/* 只比较消息引用，避免每个流事件都重渲染历史消息。 */
export const MessageRow = memo(function MessageRow({ m }: { m: SessionMessageOut }) {
  if (m.role === "USER")
    return <UserTurn question={m.question} attachments={m.attachments ?? []} />;
  return (
    <div className="max-w-full">
      <AssistantCard msg={m} />
    </div>
  );
});
