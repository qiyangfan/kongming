/* 团队管理页提供团队列表、创建、成员维护和删除确认；最后一位 OWNER 受服务端保护。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";

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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FormRow } from "@/components/form-row";
import { PageHeader } from "@/components/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import {
  addTeamMember,
  changeTeamMemberRole,
  createTeam,
  deleteTeam,
  getTeam,
  listMyTeams,
  removeTeamMember,
  type TeamItem,
} from "@/lib/team-api";
import { systemApi } from "@/lib/system-api";
import { formatDate } from "@/lib/datetime";

export default function TeamsPage() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ teamKey: "", name: "" });
  const [formError, setFormError] = useState("");
  const [managing, setManaging] = useState<TeamItem | null>(null);
  const [memberForm, setMemberForm] = useState({ userId: "", role: "VIEWER" });
  const [memberError, setMemberError] = useState("");
  const [deleting, setDeleting] = useState<TeamItem | null>(null);
  const [deleteError, setDeleteError] = useState("");

  const teams = useQuery({ queryKey: ["admin", "teams"], queryFn: listMyTeams });
  const detail = useQuery({
    queryKey: ["admin", "team", managing?.id],
    queryFn: () => getTeam(managing!.id),
    enabled: managing !== null,
  });
  // 加成员候选(复用用户清单)
  const users = useQuery({
    queryKey: ["admin", "users", "team-candidates"],
    queryFn: () => systemApi.listUsers(1, 200, ""),
    enabled: managing !== null,
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "teams"] });
    void qc.invalidateQueries({ queryKey: ["admin", "team", managing?.id] });
  };

  const create = useMutation({
    mutationFn: () => createTeam({ team_key: form.teamKey.trim(), name: form.name.trim() }),
    onSuccess: () => {
      setCreating(false);
      setForm({ teamKey: "", name: "" });
      setFormError("");
      invalidate();
    },
    onError: (e) => setFormError(e instanceof Error ? e.message : "创建失败"),
  });

  const addMember = useMutation({
    mutationFn: () => addTeamMember(managing!.id, memberForm.userId, memberForm.role),
    onSuccess: () => {
      setMemberForm({ userId: "", role: "VIEWER" });
      setMemberError("");
      invalidate();
    },
    onError: (e) => setMemberError(e instanceof Error ? e.message : "添加失败"),
  });

  const changeRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      changeTeamMemberRole(managing!.id, userId, role),
    onSuccess: () => setMemberError(""),
    onError: (e) => setMemberError(e instanceof Error ? e.message : "改角色失败"),
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => removeTeamMember(managing!.id, userId),
    onSuccess: () => setMemberError(""),
    onError: (e) => setMemberError(e instanceof Error ? e.message : "移除失败"),
  });
  const [memberConfirm, setMemberConfirm] = useState<string | null>(null);

  const remove = useMutation({
    mutationFn: () => deleteTeam(deleting!.id),
    onSuccess: () => {
      setDeleting(null);
      setDeleteError("");
      invalidate();
    },
    onError: (e) => setDeleteError(e instanceof Error ? e.message : "删除失败"),
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="团队"
        description="把助手、工作流等资源归属到团队,成员按角色共用(负责人/成员/观察者);团队记忆由成员共同维护。"
        actions={
          <Button
            onClick={() => {
              setCreating(true);
              setFormError("");
            }}
          >
            <Plus aria-hidden />
            新建团队
          </Button>
        }
      />

      {teams.isError && <ErrorAlert />}

      <DataView
        rows={teams.data ?? []}
        rowKey={(row) => row.id}
        isLoading={teams.isPending}
        isEmpty={!teams.isPending && !teams.isError && (teams.data ?? []).length === 0}
        emptyText="尚未加入任何团队。"
        columns={[
          {
            key: "team_key",
            header: "标识",
            cardPriority: 3,
            cellClassName: "font-mono text-xs",
            render: (row) => row.team_key,
          },
          {
            key: "name",
            header: "名称",
            cardPriority: 1,
            render: (row) => (
              <span className="block max-w-40 truncate" title={row.name}>
                {row.name}
              </span>
            ),
          },
          {
            key: "created_at",
            header: "创建时间",
            cardPriority: 2,
            cellClassName: "text-xs text-muted-foreground",
            render: (row) => formatDate(row.created_at),
          },
        ]}
        actions={[
          {
            label: "成员",
            onSelect: (row) => {
              setManaging(row);
              setMemberError("");
            },
          },
          {
            label: "删除",
            destructive: true,
            onSelect: (row) => {
              setDeleting(row);
              setDeleteError("");
            },
          },
        ]}
      />

      {/* 新建 */}
      <Dialog
        open={creating}
        onOpenChange={(o) => {
          if (create.isPending) return;
          setCreating(o);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建团队</DialogTitle>
          </DialogHeader>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <FormRow label="标识" htmlFor="team-key" help="小写字母开头,创建后不可改。">
              <Input
                id="team-key"
                value={form.teamKey}
                onChange={(e) => setForm({ ...form, teamKey: e.target.value })}
                className="font-mono"
                placeholder="小写字母开头"
              />
            </FormRow>
            <FormRow label="名称" htmlFor="team-name">
              <Input
                id="team-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </FormRow>
            {formError && (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            )}
            <DialogFooter>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending && <Loader2 className="animate-spin" aria-hidden />}
                创建
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 成员管理 */}
      <Dialog
        open={managing !== null}
        onOpenChange={(o) => {
          if (!o) setManaging(null);
        }}
      >
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>成员管理「{managing?.name}」</DialogTitle>
            <DialogDescription>
              负责人全权/成员可编辑团队资源/观察者可见可运行;末位负责人不可移除。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {detail.isError && <ErrorAlert />}
            <DataView
              rows={detail.data?.members ?? []}
              rowKey={(m) => m.user_id}
              columns={[
                {
                  key: "username",
                  header: "用户",
                  cardPriority: 1,
                  render: (m) => (
                    <span className="block max-w-40 truncate text-sm" title={m.username}>
                      {m.username}
                    </span>
                  ),
                },
                {
                  key: "role",
                  header: "角色",
                  cardPriority: 2,
                  render: (m) => (
                    <Select
                      value={m.role}
                      onValueChange={(v) => changeRole.mutate({ userId: m.user_id, role: v })}
                    >
                      <SelectTrigger size="sm" className="w-28" aria-label={`角色 ${m.username}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="OWNER">负责人</SelectItem>
                        <SelectItem value="MEMBER">成员</SelectItem>
                        <SelectItem value="VIEWER">观察者</SelectItem>
                      </SelectContent>
                    </Select>
                  ),
                },
              ]}
              actions={[
                {
                  // 移除成员二次确认(首击选中,再击执行),武装态
                  // 经行级 label 可视;挂起期仍以 onSelect 守卫兜底
                  label: (m) => (memberConfirm === m.user_id ? "再次点击确认移除" : "移除"),
                  destructive: true,
                  onSelect: (m) => {
                    if (removeMember.isPending) return;
                    if (memberConfirm === m.user_id) {
                      setMemberConfirm(null);
                      removeMember.mutate(m.user_id);
                    } else {
                      setMemberConfirm(m.user_id);
                    }
                  },
                },
              ]}
            />
            <form
              className="flex items-end gap-2 border-t pt-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (memberForm.userId) addMember.mutate();
              }}
            >
              <div className="flex-1 space-y-1">
                <Label>添加成员</Label>
                <Select
                  value={memberForm.userId}
                  onValueChange={(v) => setMemberForm({ ...memberForm, userId: v })}
                >
                  <SelectTrigger aria-label="选择用户">
                    <SelectValue placeholder="选择用户" />
                  </SelectTrigger>
                  <SelectContent>
                    {(users.data?.items ?? [])
                      .filter(
                        (u) =>
                          // 按不可变的 user_id 排除已是成员的用户,避免用户名变更造成误判
                          !(detail.data?.members ?? []).some((m) => m.user_id === u.id),
                      )
                      .map((u) => (
                        <SelectItem key={u.id} value={u.id}>
                          {u.username}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <Select
                value={memberForm.role}
                onValueChange={(v) => setMemberForm({ ...memberForm, role: v })}
              >
                <SelectTrigger className="w-28" aria-label="角色">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="OWNER">负责人</SelectItem>
                  <SelectItem value="MEMBER">成员</SelectItem>
                  <SelectItem value="VIEWER">观察者</SelectItem>
                </SelectContent>
              </Select>
              <Button type="submit" disabled={addMember.isPending || !memberForm.userId}>
                {addMember.isPending && <Loader2 className="animate-spin" aria-hidden />}
                添加
              </Button>
            </form>
            {memberError && (
              <p role="alert" className="text-sm text-destructive">
                {memberError}
              </p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <AlertDialog
        open={deleting !== null}
        onOpenChange={(o) => {
          if (remove.isPending) return;
          if (!o) setDeleting(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除团队「{deleting?.name}」?</AlertDialogTitle>
            <AlertDialogDescription>
              成员关系解除;归属团队的 Bot/工作流自动回到个人(创建者可见,不丢资源)。 此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteError && (
            <p role="alert" className="text-sm text-destructive">
              {deleteError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>再想想</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认删除
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
