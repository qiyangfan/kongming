/* 审计行详情对话框:SQL 原文与拦截原因,或操作日志的原始 JSON。 */

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

import type { OperItem, SqlItem } from "./audit-tables";

export type AuditDetail = { kind: "sql"; item: SqlItem } | { kind: "oper"; item: OperItem } | null;

/** SQL 详情:原文、拦截原因与链路 ID。 */
function SqlDetail({ item }: { item: SqlItem }) {
  return (
    <div>
      <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
        {item.sql_text}
      </pre>
      {item.block_reason && (
        <p className="mt-1 text-xs text-muted-foreground">拦截原因:{item.block_reason}</p>
      )}
      <p
        className="mt-1 max-w-full truncate font-mono text-xs text-muted-foreground"
        title={item.trace_id ?? undefined}
      >
        链路 ID:{item.trace_id ?? "—"}
      </p>
    </div>
  );
}

/** 操作详情:管理排查场景需要原始 JSON。 */
function OperDetail({ item }: { item: OperItem }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted-foreground">原始数据(JSON)</p>
      <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
        {JSON.stringify(item.detail ?? {}, null, 2)}
      </pre>
    </div>
  );
}

/** 行详情对话框(DataView 不承载行内展开内容)。 */
export function AuditDetailDialog({
  detail,
  onClose,
}: {
  detail: AuditDetail;
  onClose: () => void;
}) {
  return (
    <Dialog open={detail !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined} className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{detail?.kind === "sql" ? "SQL 详情" : "操作详情"}</DialogTitle>
        </DialogHeader>
        {detail?.kind === "sql" && <SqlDetail item={detail.item} />}
        {detail?.kind === "oper" && <OperDetail item={detail.item} />}
      </DialogContent>
    </Dialog>
  );
}
