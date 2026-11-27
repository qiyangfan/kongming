/* 用户只读身份视图:账户由 Keycloak 管,租户/成员/粗角色归锦官;本地不可编辑。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import type { UserItemOut } from "@kongming/api-client/system-api";
import { systemApi } from "@kongming/api-client/system-api";
import { formatDate } from "@kongming/api-client/datetime";
import { roleLabel } from "@kongming/api-client/role-labels";
import { Badge } from "@kongming/ui/badge";
import { Button } from "@kongming/ui/button";
import { DataView, type DataViewColumn } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { Input } from "@kongming/ui/input";
import { PageHeader } from "@kongming/ui/page-header";
import { Pagination } from "@kongming/ui/pagination";

const PAGE_SIZE = 10;

const columns: DataViewColumn<UserItemOut>[] = [
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
      <Badge variant={u.status === "ACTIVE" ? "secondary" : "outline"}>
        {u.status === "ACTIVE" ? "启用" : "停用"}
      </Badge>
    ),
  },
  {
    key: "role_keys",
    header: "角色",
    cardPriority: 4,
    render: (u) => (
      <div className="flex flex-wrap gap-1">
        {u.role_keys.length === 0 ? (
          <span className="text-sm text-muted-foreground">—</span>
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
    render: (u) => <span>{u.dept_name ?? "—"}</span>,
  },
  {
    key: "created_at",
    header: "创建时间",
    cellClassName: "text-muted-foreground",
    render: (u) => formatDate(u.created_at),
  },
];

export default function UsersPage() {
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [query, setQuery] = useState("");
  const users = useQuery({
    queryKey: ["admin-users", page, query],
    queryFn: () => systemApi.listUsers(page, PAGE_SIZE, query),
  });
  const total = users.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-3">
      <PageHeader
        title="用户"
        description="只读身份视图:账号由统一身份管理,租户/成员/粗角色来自锦官快照;本地只维护角色、部门与团队。"
      />
      <div className="flex items-center gap-2">
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setPage(1);
              setQuery(keyword);
            }
          }}
          placeholder="搜索用户名/昵称,回车确认"
          className="max-w-64"
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setPage(1);
            setQuery(keyword);
          }}
        >
          搜索
        </Button>
      </div>
      {users.isError && <ErrorAlert text="用户列表加载失败,请稍后重试。" />}
      <DataView
        rows={users.data?.items ?? []}
        rowKey={(u) => u.id}
        isLoading={users.isPending}
        isEmpty={!users.isPending && (users.data?.items.length ?? 0) === 0}
        emptyText="无匹配用户"
        columns={columns}
      />
      <Pagination page={page} pageCount={pages} total={total} onPageChange={setPage} />
    </div>
  );
}
