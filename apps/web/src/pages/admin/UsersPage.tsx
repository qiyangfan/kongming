/* 管理用户的角色、部门和启停状态，并支持重置密码和删除用户。 */

import { Button } from "@/components/ui/button";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";

import { DeleteUserDialog, UserFormDialog } from "./users/UserDialogs";
import { userColumns } from "./users/UserColumns";
import {
  flattenDepts,
  PAGE_SIZE,
  removeError,
  useUsersPageState,
} from "./users/use-users-page-state";

export default function UsersPage() {
  const s = useUsersPageState();
  const { danger } = s;
  const total = s.users.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const runSearch = () => s.runSearch();

  return (
    <div className="space-y-3">
      <PageHeader
        title="用户"
        description="本地授权资料管理:分配角色与部门、启用停用;账号由虎符登录建档。"
      />
      <div className="flex items-center gap-2">
        <Input
          value={s.keyword}
          onChange={(e) => s.setKeyword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") runSearch();
          }}
          placeholder="搜索用户名/昵称,回车确认"
          className="max-w-64"
        />
        <Button variant="outline" size="sm" onClick={runSearch}>
          搜索
        </Button>
      </div>

      {s.users.isError && <ErrorAlert text={s.usersErrorText} />}

      <DataView
        rows={s.users.data?.items ?? []}
        rowKey={(u) => u.id}
        isLoading={s.users.isPending}
        isEmpty={!s.users.isPending && s.users.data?.items.length === 0}
        emptyText="无匹配用户"
        columns={userColumns({
          togglePending: s.toggle.isPending,
          toggleId: s.toggleId,
          toggleErr: s.toggleErr,
          onToggle: (u) => s.toggle.mutate(u),
        })}
        actions={[
          { label: "编辑", onSelect: s.openEdit },
          { label: "删除", destructive: true, onSelect: danger.setDeleting },
        ]}
      />

      <Pagination page={s.page} pageCount={pages} total={total} onPageChange={s.setPage} />

      <UserFormDialog
        open={s.formOpen}
        creating={false}
        form={s.form}
        onFormChange={s.setForm}
        formError={s.formError}
        tenantOptions={s.tenants.data ?? []}
        deptOptions={flattenDepts(s.depts.data ?? [])}
        roles={s.roles.data ?? []}
        pending={s.formPending}
        onSubmit={s.submit}
        onClose={s.closeForm}
      />

      <DeleteUserDialog
        username={danger.deletingName}
        error={removeError(danger.remove)}
        pending={danger.remove.isPending}
        onConfirm={danger.confirmRemove}
        onClose={danger.closeDelete}
      />
    </div>
  );
}
