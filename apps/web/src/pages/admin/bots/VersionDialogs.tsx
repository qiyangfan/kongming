/* 版本历史对话框与回滚确认框;回滚视同上架,须重新审核通过才生效。 */

import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorAlert } from "@/components/error-alert";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { BotVersion } from "@/lib/store-api";

export function VersionHistoryDialog({
  open,
  versions,
  isPending,
  isError,
  rollbackPending,
  onRollback,
  onClose,
}: {
  open: boolean;
  versions: BotVersion[];
  isPending: boolean;
  isError: boolean;
  rollbackPending: boolean;
  onRollback: (versionNo: number) => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>版本历史</DialogTitle>
          <DialogDescription>
            每次上架生成一条不可变版本快照;商店始终使用「当前上架版本」。回滚视同上架:按现行安全规则重新审核后切换,通过才生效。
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-96">
          <div className="space-y-2 pr-2">
            {isPending && (
              <p className="py-4 text-center text-xs text-muted-foreground">版本加载中…</p>
            )}
            {isError && <ErrorAlert text="版本历史加载失败,请关闭后重试" />}
            {versions.length === 0 && (
              <p className="py-4 text-center text-xs text-muted-foreground">
                暂无版本——上架后这里会记录每次上架的快照
              </p>
            )}
            {versions.map((v) => (
              <div
                key={v.version_no}
                className="flex items-start justify-between gap-2 rounded-md border p-3"
              >
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5">
                    <span className="font-mono text-sm font-medium">v{v.version_no}</span>
                    {v.current && <Badge variant="default">当前上架版本</Badge>}
                  </p>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">
                    {new Date(v.published_at).toLocaleString()} · {v.snapshot.name}
                    {v.snapshot.greeting ? ` · ${v.snapshot.greeting}` : ""}
                  </p>
                </div>
                {v.current ? (
                  <Button size="sm" variant="outline" disabled>
                    当前
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={rollbackPending}
                    onClick={() => onRollback(v.version_no)}
                  >
                    回滚到此版本
                  </Button>
                )}
              </div>
            ))}
          </div>
        </ScrollArea>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function RollbackDialog({
  versionNo,
  error,
  pending,
  onConfirm,
  onClose,
}: {
  versionNo: number | null;
  error: string;
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <AlertDialog
      open={versionNo !== null}
      onOpenChange={(open) => {
        // 进行中不可关闭(Esc 中途关框会让回滚失败信息无处渲染,
        // 用户误以为已生效);未进行中才允许取消
        if (!open && !pending) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>回滚到 v{versionNo ?? ""}?</AlertDialogTitle>
          <AlertDialogDescription>
            回滚会把该历史版本重新指为当前上架版本,商店展示与试运行随之切换(该版本保留在历史中)。回滚按现行安全规则重新审核,审核不通过则拒绝且回滚不生效。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p className="text-destructive text-sm" role="alert">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>取消</AlertDialogCancel>
          <Button onClick={onConfirm}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            确认回滚
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
