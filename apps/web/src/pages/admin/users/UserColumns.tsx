/* 用户列表的列定义。 */

import { Badge } from "@/components/ui/badge";
import type { DataViewColumn } from "@/components/data-view";
import { StatusSwitch } from "@/components/status-switch";
import { formatDate } from "@/lib/datetime";
import { roleLabel } from "@/lib/role-labels";
import type { UserItemOut } from "@/lib/system-api";

export function userColumns(src: {
  togglePending: boolean;
  toggleId: string | null;
  toggleErr: string | null;
  onToggle: (u: UserItemOut) => void;
}): DataViewColumn<UserItemOut>[] {
  return [
    {
      key: "username",
      header: "用户名",
      cardPriority: 1,
      render: (u) => (
        <span className="block max-w-40 truncate font-medium" title={u.username}>
          {u.username}
        </span>
      ),
    },
    {
      key: "nickname",
      header: "昵称",
      cardPriority: 2,
      render: (u) => (
        <span className="block max-w-32 truncate" title={u.nickname}>
          {u.nickname}
        </span>
      ),
    },
    {
      key: "status",
      header: "状态",
      cardPriority: 3,
      render: (u) => (
        <StatusSwitch
          active={u.status === "ACTIVE"}
          pending={src.togglePending}
          error={!src.togglePending && src.toggleId === u.id ? src.toggleErr : null}
          onToggle={() => src.onToggle(u)}
          ariaLabel={`${u.username} 启用状态`}
        />
      ),
    },
    {
      key: "role_keys",
      header: "角色",
      cardPriority: 4,
      render: (u) => (
        <div className="flex flex-wrap gap-1">
          {u.role_keys.length === 0 ? (
            <span className="text-muted-foreground text-sm">—</span>
          ) : (
            u.role_keys.map((k) => (
              <Badge key={k} variant="outline">
                {roleLabel(k)}
              </Badge>
            ))
          )}
        </div>
      ),
    },
    {
      key: "dept_name",
      header: "部门",
      render: (u) => (
        <span className="block max-w-32 truncate" title={u.dept_name ?? undefined}>
          {u.dept_name ?? "—"}
        </span>
      ),
    },
    {
      key: "created_at",
      header: "创建时间",
      cellClassName: "text-muted-foreground",
      render: (u) => formatDate(u.created_at),
    },
  ];
}
