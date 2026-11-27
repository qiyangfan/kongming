/* 创建条件分支连线时必须选择 true 或 false；不能保存缺少 on 值的连线。 */

import { Loader2 } from "lucide-react";

import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { cn } from "@kongming/api-client/utils";

export interface PendingConditionEdge {
  source: string;
  target: string;
  /** 目标已有条件入边时的现值(预选提示;同目标条件入边单一 on 语义) */
  existingOn: "true" | "false" | null;
}

export function ConditionOnDialog({
  pending,
  isPending,
  onConfirm,
  onCancel,
}: {
  pending: PendingConditionEdge | null;
  isPending?: boolean;
  onConfirm: (on: "true" | "false") => void;
  onCancel: () => void;
}) {
  return (
    <Dialog
      open={pending !== null}
      onOpenChange={(o) => {
        if (isPending) return;
        if (!o) onCancel();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>指定条件分支 on 值</DialogTitle>
          <DialogDescription>
            条件节点的后继必须显式携带 on 值:条件成立走 true 分支,不成立走 false
            分支。同节点的多条条件入边共用同一个 on 值(节点级语义)。
          </DialogDescription>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          <span className="font-mono">{pending?.source}</span> →{" "}
          <span className="font-mono">{pending?.target}</span>
        </p>
        <div className="grid grid-cols-2 gap-3">
          {(["true", "false"] as const).map((on) => (
            <Button
              key={on}
              type="button"
              variant="outline"
              disabled={isPending}
              onClick={() => onConfirm(on)}
              className={cn(
                "h-auto flex-col gap-1 py-4",
                pending?.existingOn === on && "border-ring ring-2 ring-ring/40",
              )}
            >
              <span className="font-mono text-sm">on={on}</span>
              <span className="text-xs font-normal text-muted-foreground">
                {on === "true" ? "条件成立走此分支" : "条件不成立走此分支"}
              </span>
            </Button>
          ))}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            disabled={isPending}
            onClick={onCancel}
          >
            取消连线
          </Button>
          {isPending && <Loader2 className="animate-spin" aria-hidden />}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
