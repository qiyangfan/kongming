/* 触发器页的辅助弹窗:webhook 创建结果展示与删除确认。 */

import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@kongming/ui/alert-dialog";
import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import type { TriggerItem } from "@kongming/api-client/trigger-api";

type CreatedInfo = { webhook_path: string | null; secret?: string } | null;

export function WebhookCreatedDialog({
  created,
  onClose,
}: {
  created: CreatedInfo;
  onClose: () => void;
}) {
  return (
    <Dialog open={created !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>webhook 已创建</DialogTitle>
          <DialogDescription>
            以下信息仅此一次展示,请立即保存;密钥不回显于任何列表。
          </DialogDescription>
        </DialogHeader>
        {created && (
          <div className="space-y-2 text-sm">
            <p className="break-all font-mono text-xs">
              URL: {location.origin}/webhooks/{created.webhook_path}
            </p>
            {created.secret && (
              <p className="break-all font-mono text-xs">密钥: {created.secret}</p>
            )}
            <p className="text-xs text-muted-foreground">
              签名校验:请求头 X-Signature 的值为 HMAC-SHA256(密钥, 请求体) 的十六进制串。
            </p>
          </div>
        )}
        <DialogFooter>
          <Button onClick={onClose}>我已保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function TriggerDeleteDialog({
  target,
  error,
  pending,
  onClose,
  onConfirm,
}: {
  target: TriggerItem | null;
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
          <AlertDialogTitle>删除触发器「{target?.name}」?</AlertDialogTitle>
          <AlertDialogDescription>
            删除后不再触发,webhook 地址随之失效(历史记录保留)。此操作不可恢复。
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
