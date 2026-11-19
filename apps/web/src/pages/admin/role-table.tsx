/* 角色列表:键/名称/数据范围/SQL 范围/排序/权限点数与行操作;删除确认单独成件。 */

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
import { DataView } from "@/components/data-view";
import type { RoleOut } from "@/lib/system-api";

import { SQL_SCOPES, scopeLabel } from "./role-logic";

interface RoleTableProps {
  rows: RoleOut[];
  isPending: boolean;
  onEdit: (r: RoleOut) => void;
  onBind: (r: RoleOut) => void;
  onDelete: (r: RoleOut) => void;
}

/** 角色键 + 内置标记。 */
function RoleKeyCell({ row }: { row: RoleOut }) {
  return (
    <span className="flex max-w-40 items-center gap-1.5 font-medium">
      <span title={row.role_key} className="truncate">
        {row.role_key}
      </span>
      {row.builtin && (
        <Badge variant="secondary" className="shrink-0">
          内置
        </Badge>
      )}
    </span>
  );
}

/** 角色表。 */
export function RoleTable({ rows, isPending, onEdit, onBind, onDelete }: RoleTableProps) {
  return (
    <DataView
      rows={rows}
      rowKey={(r) => r.id}
      isLoading={isPending}
      isEmpty={!isPending && rows.length === 0}
      emptyText="暂无角色"
      columns={[
        {
          key: "role_key",
          header: "角色键",
          cardPriority: 1,
          render: (r) => <RoleKeyCell row={r} />,
        },
        {
          key: "role_name",
          header: "名称",
          cardPriority: 2,
          render: (r) => (
            <span className="block max-w-40 truncate" title={r.role_name}>
              {r.role_name}
            </span>
          ),
        },
        {
          key: "data_scope",
          header: "数据范围",
          cardPriority: 3,
          render: (r) => scopeLabel(r),
        },
        {
          key: "sql_scope",
          header: "SQL 范围",
          cardPriority: 4,
          render: (r) => SQL_SCOPES.find((s) => s.value === r.sql_scope)?.label ?? r.sql_scope,
        },
        { key: "sort", header: "排序", render: (r) => r.sort },
        { key: "perm_ids", header: "权限点", render: (r) => `${r.perm_ids.length} 项` },
      ]}
      actions={[
        { label: "编辑", onSelect: onEdit },
        { label: "权限", onSelect: onBind },
        {
          label: "删除",
          destructive: true,
          // 内置角色不可删除:行级禁用承担可视反馈与拦截
          isDisabled: (r) => r.builtin,
          disabledReason: "内置角色不可删除",
          onSelect: onDelete,
        },
      ]}
    />
  );
}

interface DeleteRoleDialogProps {
  deleting: RoleOut | null;
  onClose: () => void;
  pending: boolean;
  error: string;
  onConfirm: () => void;
}

/** 删除角色确认:成功才关,失败保持开启展示上方错误(Radix Action 点击即关)。 */
export function DeleteRoleDialog(props: DeleteRoleDialogProps) {
  return (
    <AlertDialog open={props.deleting !== null} onOpenChange={(open) => !open && props.onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除角色 {props.deleting?.role_key}?</AlertDialogTitle>
          <AlertDialogDescription>
            仍有账号在使用的角色无法删除;删除后其权限配置一并清除。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {props.error && (
          <p className="text-destructive text-sm" role="alert">
            {props.error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button variant="destructive" onClick={props.onConfirm} disabled={props.pending}>
            {props.pending ? "删除中…" : "删除"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
