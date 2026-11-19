/* 角色权限绑定对话框:按命名空间分组的权限点勾选,保存为全量覆盖。 */

import { Button } from "@/components/ui/button";
import { CheckList } from "@/components/check-list";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { PermOut, RoleOut } from "@/lib/system-api";

import { groupPerms } from "./role-logic";

interface RolePermBindDialogProps {
  binding: RoleOut | null;
  perms: PermOut[];
  boundIds: string[];
  setBoundIds: (ids: string[]) => void;
  bindError: string;
  pending: boolean;
  onClose: () => void;
  onSave: () => void;
}

/** 权限绑定对话框。 */
export function RolePermBindDialog(props: RolePermBindDialogProps) {
  const grouped = groupPerms(props.perms);
  return (
    <Dialog open={props.binding !== null} onOpenChange={(open) => !open && props.onClose()}>
      <DialogContent aria-describedby={undefined} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>权限绑定 — {props.binding?.role_name}</DialogTitle>
        </DialogHeader>
        {props.bindError && (
          <p className="text-destructive text-sm" role="alert">
            {props.bindError}
          </p>
        )}
        <div className="max-h-80 space-y-3 overflow-y-auto">
          {grouped.map(([ns, list]) => (
            <div key={ns} className="min-w-0 space-y-1.5">
              <p className="text-muted-foreground text-xs font-medium">{ns}</p>
              <CheckList
                className="max-h-none"
                options={list.map((p) => ({ id: p.id, label: p.perm_name, hint: p.perm_code }))}
                selected={props.boundIds}
                onChange={props.setBoundIds}
                emptyText="该命名空间无权限点"
              />
            </div>
          ))}
        </div>
        <p className="text-muted-foreground text-xs">保存为全量覆盖:未勾选项将被移除。</p>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={props.onClose}>
            取消
          </Button>
          <Button onClick={props.onSave} disabled={props.pending}>
            {props.pending ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
