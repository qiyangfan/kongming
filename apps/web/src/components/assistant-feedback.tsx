/* 问答卡片内的反馈行与指正弹窗；反馈写入后同步所有消息缓存。 */

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, ThumbsDown, ThumbsUp } from "lucide-react";

import type { SessionMessageOut } from "@/lib/api";
import { submitFeedback } from "@/lib/feedback-api";
import { useAuth } from "@/lib/auth-store";
import { hasPerm } from "@/lib/nav";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

import type { FeedbackOut } from "@/lib/feedback-api";

type Rating = "UP" | "DOWN";

type FeedbackPayload = {
  rating: Rating;
  comment?: string | null;
  corrected_sql?: string | null;
};

/* 用反馈返回值替换目标消息的评分、纠错 SQL 与留言，其余条目原样保留。 */
function patchMessages(old: unknown, mid: string, data: FeedbackOut): unknown {
  if (!Array.isArray(old)) return old;
  return old.map((m) =>
    m && typeof m === "object" && (m as { id?: string }).id === mid
      ? {
          ...m,
          rating: data.rating,
          corrected_sql: data.corrected_sql,
          feedback_comment: data.comment,
        }
      : m,
  );
}

function feedbackErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "反馈未成,请稍后再试。";
}

/* 反馈行：赞立即提交，踩可编辑说明和纠错 SQL；无权限或无消息 ID 时不显示。 */
export function FeedbackActions({ msg }: { msg: SessionMessageOut }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!hasPerm(user, "chat:feedback:submit") || !msg.id) return null;
  const mid = msg.id;

  async function submit(payload: FeedbackPayload): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      const data = await submitFeedback(mid, payload);
      // 实时消息经 setQueriesData 更新所有含该消息的缓存,激活态即时回显
      qc.setQueriesData({ queryKey: ["messages"] }, (old: unknown) =>
        patchMessages(old, mid, data),
      );
      return true;
    } catch (err) {
      setError(feedbackErrorMessage(err));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submitDown(comment: string | null, correctedSql: string): Promise<void> {
    const ok = await submit({
      rating: "DOWN",
      comment,
      corrected_sql: correctedSql,
    });
    if (ok) setDialogOpen(false);
  }

  function handleOpenChange(o: boolean): void {
    if (busy) return; // 提交中不可关闭
    setDialogOpen(o);
  }

  const up = msg.rating === "UP";
  const down = msg.rating === "DOWN";

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="有用"
        aria-pressed={up}
        title="有用"
        disabled={busy}
        onClick={() => void submit({ rating: "UP" })}
        className={up ? "text-primary" : "text-muted-foreground"}
      >
        <ThumbsUp aria-hidden className={up ? "fill-current" : undefined} />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="不准确"
        aria-pressed={down}
        title="不准确"
        disabled={busy}
        onClick={() => setDialogOpen(true)}
        className={down ? "text-primary" : "text-muted-foreground"}
      >
        <ThumbsDown aria-hidden className={down ? "fill-current" : undefined} />
      </Button>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <FeedbackDialog
        msg={msg}
        open={dialogOpen}
        busy={busy}
        onOpenChange={handleOpenChange}
        onSubmit={submitDown}
      />
    </div>
  );
}

/* 弹窗打开时按消息现值回填留言与 SQL；每次打开都重置「留言已触碰」标记。 */
function useFeedbackDraft(
  msg: SessionMessageOut,
  open: boolean,
): {
  comment: string;
  sql: string;
  commentTouched: boolean;
  setComment: (v: string) => void;
  setSql: (v: string) => void;
} {
  const [comment, setComment] = useState("");
  const [sql, setSql] = useState("");
  const [commentTouched, setCommentTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setComment(msg.feedback_comment ?? "");
      setSql(msg.corrected_sql ?? msg.sql ?? "");
      setCommentTouched(false);
    }
  }, [open, msg.feedback_comment, msg.corrected_sql, msg.sql]);

  function onComment(v: string): void {
    setComment(v);
    setCommentTouched(true);
  }

  return { comment, sql, commentTouched, setComment: onComment, setSql };
}

/* 点踩弹窗预填已有说明和纠错 SQL；未修改字段保持原值，清空字段则提交删除。 */
function FeedbackDialog({
  msg,
  open,
  busy,
  onOpenChange,
  onSubmit,
}: {
  msg: SessionMessageOut;
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (comment: string | null, correctedSql: string) => Promise<void>;
}) {
  const draft = useFeedbackDraft(msg, open);

  function handleSubmit(e: React.FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    // comment 未触碰传 null=保持现值;触碰后按显式值(空串=清空)
    void onSubmit(draft.commentTouched ? draft.comment : null, draft.sql);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>此答不准确?</DialogTitle>
          <DialogDescription>
            留下指正,军师会记住——纠错 SQL 将作为同类问题的参考。
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={handleSubmit}>
          <div className="space-y-1.5">
            <label htmlFor="fb-comment" className="text-sm text-muted-foreground">
              留言(可留空)
            </label>
            <Textarea
              id="fb-comment"
              value={draft.comment}
              onChange={(e) => draft.setComment(e.target.value)}
              placeholder="哪里不对?"
              rows={2}
              maxLength={500}
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="fb-sql" className="text-sm text-muted-foreground">
              正确的 SQL(清空即撤销纠错)
            </label>
            <Textarea
              id="fb-sql"
              value={draft.sql}
              onChange={(e) => draft.setSql(e.target.value)}
              rows={4}
              maxLength={4000}
              className="font-mono text-xs"
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              提交指正
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
