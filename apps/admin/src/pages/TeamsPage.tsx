/* 团队管理页提供团队列表、创建、成员维护和删除确认；最后一位 OWNER 受服务端保护。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { PageHeader } from "@kongming/ui/page-header";
import { ErrorAlert } from "@kongming/ui/error-alert";
import {
  addTeamMember,
  changeTeamMemberRole,
  createTeam,
  deleteTeam,
  getTeam,
  listMyTeams,
  removeTeamMember,
  type TeamItem,
} from "@kongming/api-client/team-api";
import { systemApi } from "@kongming/api-client/system-api";

import { CreateTeamDialog, DeleteTeamDialog, MemberManagementDialog } from "./team-dialogs";
import { TeamTable } from "./team-table";

const EMPTY_MEMBER_FORM = { userId: "", role: "VIEWER" };

export default function TeamsPage() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ teamKey: "", name: "" });
  const [formError, setFormError] = useState("");
  const [managing, setManaging] = useState<TeamItem | null>(null);
  const [memberForm, setMemberForm] = useState(EMPTY_MEMBER_FORM);
  const [memberError, setMemberError] = useState("");
  const [memberConfirm, setMemberConfirm] = useState<string | null>(null);
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
  const errText = (fallback: string) => (e: unknown) => (e instanceof Error ? e.message : fallback);

  const create = useMutation({
    mutationFn: () => createTeam({ team_key: form.teamKey.trim(), name: form.name.trim() }),
    onSuccess: () => {
      setCreating(false);
      setForm({ teamKey: "", name: "" });
      setFormError("");
      invalidate();
    },
    onError: errText("创建失败"),
  });

  const addMember = useMutation({
    mutationFn: () => addTeamMember(managing!.id, memberForm.userId, memberForm.role),
    onSuccess: () => {
      setMemberForm(EMPTY_MEMBER_FORM);
      setMemberError("");
      invalidate();
    },
    onError: errText("添加失败"),
  });

  const changeRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      changeTeamMemberRole(managing!.id, userId, role),
    onSuccess: () => setMemberError(""),
    onError: errText("改角色失败"),
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => removeTeamMember(managing!.id, userId),
    onSuccess: () => setMemberError(""),
    onError: errText("移除失败"),
  });

  const remove = useMutation({
    mutationFn: () => deleteTeam(deleting!.id),
    onSuccess: () => {
      setDeleting(null);
      setDeleteError("");
      invalidate();
    },
    onError: errText("删除失败"),
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

      <TeamTable
        rows={teams.data ?? []}
        isPending={teams.isPending}
        isError={teams.isError}
        onMembers={(row) => {
          setManaging(row);
          setMemberError("");
        }}
        onDelete={(row) => {
          setDeleting(row);
          setDeleteError("");
        }}
      />

      {/* 新建 */}
      <CreateTeamDialog
        open={creating}
        form={form}
        setForm={setForm}
        error={formError}
        pending={create.isPending}
        onOpenChange={setCreating}
        onSubmit={() => create.mutate()}
      />

      {/* 成员管理 */}
      <MemberManagementDialog
        managing={managing}
        detail={detail}
        users={(users.data?.items ?? []).map((u) => ({ id: u.id, username: u.username }))}
        memberForm={memberForm}
        setMemberForm={setMemberForm}
        memberError={memberError}
        memberConfirm={memberConfirm}
        onMemberConfirm={setMemberConfirm}
        addPending={addMember.isPending}
        removePending={removeMember.isPending}
        onClose={() => setManaging(null)}
        onAdd={() => addMember.mutate()}
        onChangeRole={(userId, role) => changeRole.mutate({ userId, role })}
        onRemove={(userId) => removeMember.mutate(userId)}
      />

      {/* 删除确认 */}
      <DeleteTeamDialog
        deleting={deleting}
        error={deleteError}
        pending={remove.isPending}
        onOpenChange={(o) => !o && setDeleting(null)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}
