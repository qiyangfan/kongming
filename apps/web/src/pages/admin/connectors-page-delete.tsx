/* 连接器删除确认:硬删并从工具注入集移除。 */

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
import { Button } from "@/components/ui/button";
import type { ConnectorOut } from "@/lib/connector-api";

export function ConnectorDeleteDialog({
  target,
  error,
  pending,
  onClose,
  onConfirm,
}: {
  target: ConnectorOut | null;
  error: string;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog
      open={target !== null}
      onOpenChange={(o) => {
        if (pending) return;
        if (!o) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除连接器「{target?.name}」?</AlertDialogTitle>
          <AlertDialogDescription>
            删除后该工具即时从注入集消失,Agent 调用立即失效;删除为硬删并落操作审计。
            此操作不可恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>再想想</AlertDialogCancel>
          <Button variant="destructive" disabled={pending} onClick={onConfirm}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            确认删除
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
