/* 反馈详情弹窗(feedback-refine spec):对照区 + 涉及表区(「编辑知识」跳转)+ 处置区(无 handle 权限隐藏) */

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Loader2 } from "lucide-react";

import { Badge } from "@kongming/ui/badge";
import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { Input } from "@kongming/ui/input";
import { Label } from "@kongming/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { displayUser } from "@kongming/api-client/display-user";
import {
  handleFeedback,
  involvedTables,
  type FeedbackPageItemOut,
} from "@kongming/api-client/feedback-api";

export function FeedbackDetailDialog({
  item,
  canHandle,
  onClose,
  onHandled,
}: {
  item: FeedbackPageItemOut;
  canHandle: boolean;
  onClose: () => void;
  onHandled: () => void;
}) {
  const [status, setStatus] = useState(item.feedback_status);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const tables = useQuery({
    queryKey: ["feedback", "involved-tables", item.message_id],
    queryFn: () => involvedTables(item.message_id),
  });

  const save = useMutation({
    mutationFn: () =>
      handleFeedback(item.message_id, {
        status,
        ...(note ? { note } : {}),
      }),
    onSuccess: () => onHandled(),
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o && !save.isPending) onClose();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>反馈详情</DialogTitle>
          <DialogDescription>
            {displayUser(item.user_nickname, item.user_username)} · 租户 {item.tenant_key} · 数据源{" "}
            {item.datasource_key}
          </DialogDescription>
        </DialogHeader>

        {/* 字段包 form 对齐既有弹窗范式;备注为单行 Input,Enter 即隐式提交 */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!canHandle || save.isPending) return;
            save.mutate();
          }}
        >
          <div className="max-h-[55vh] space-y-4 overflow-y-auto pr-1">
            <section>
              <h3 className="mb-1 text-xs font-medium text-muted-foreground">问句</h3>
              <p className="text-sm">{item.question ?? "—"}</p>
            </section>
            <section>
              <h3 className="mb-1 text-xs font-medium text-muted-foreground">原始 SQL</h3>
              <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
                {item.sql_text ?? "—"}
              </pre>
            </section>
            <section>
              <h3 className="mb-1 text-xs font-medium text-muted-foreground">纠错 SQL</h3>
              <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
                {item.corrected_sql ?? "—"}
              </pre>
            </section>
            <section>
              <h3 className="mb-1 text-xs font-medium text-muted-foreground">留言</h3>
              <p className="text-sm">{item.feedback_comment ?? "—"}</p>
            </section>

            <section>
              <h3 className="mb-1 text-xs font-medium text-muted-foreground">涉及表</h3>
              <InvolvedTablesSection tables={tables} />
            </section>

            {canHandle && (
              <section className="space-y-3 rounded-lg border border-border p-3">
                <h3 className="text-xs font-medium text-muted-foreground">处置</h3>
                <div className="flex items-end gap-3">
                  <div className="space-y-1">
                    <Label className="text-xs">状态</Label>
                    <Select value={status} onValueChange={setStatus}>
                      <SelectTrigger className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="PENDING">待处置</SelectItem>
                        <SelectItem value="ADOPTED">已采纳</SelectItem>
                        <SelectItem value="REJECTED">已驳回</SelectItem>
                        <SelectItem value="DISMISSED">已忽略</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex-1 space-y-1">
                    <Label className="text-xs">处理备注</Label>
                    <Input
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder="处置说明(可留空)"
                      maxLength={500}
                    />
                  </div>
                </div>
                {error && (
                  <p role="alert" className="text-xs text-destructive">
                    {error}
                  </p>
                )}
              </section>
            )}
          </div>

          {canHandle && (
            <DialogFooter>
              <Button type="submit" disabled={save.isPending}>
                {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
                保存处置
              </Button>
            </DialogFooter>
          )}
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** 涉及表区:定位中/SQL 解析失败/未定位到表/表徽章(有知识时可跳转编辑)。 */
function InvolvedTablesSection({
  tables,
}: {
  tables: ReturnType<typeof useQuery<Awaited<ReturnType<typeof involvedTables>>>>;
}) {
  if (tables.isLoading) {
    return (
      <p className="text-xs text-muted-foreground">
        <Loader2 className="inline size-3 animate-spin" aria-hidden /> 定位中…
      </p>
    );
  }
  if (tables.data?.parse_error) {
    return <p className="text-xs text-muted-foreground">SQL 解析失败,请手动前往知识库编辑。</p>;
  }
  if ((tables.data?.tables.length ?? 0) === 0) {
    return <p className="text-xs text-muted-foreground">未定位到表。</p>;
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      {tables.data?.tables.map((t) => (
        <span key={t.table_name} className="inline-flex items-center gap-1.5">
          <Badge variant="outline">{t.table_name}</Badge>
          {t.has_knowledge && (
            <Button variant="link" size="xs" asChild>
              <Link to={`/rag?table=${encodeURIComponent(t.table_name)}`}>编辑知识</Link>
            </Button>
          )}
        </span>
      ))}
    </div>
  );
}
