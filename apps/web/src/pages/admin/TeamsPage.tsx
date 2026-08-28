/* 团队管理页(032 team-workspace spec):
   我所在的团队列表 + 创建 + 成员管理对话框(加/删/改角色,末位 OWNER
   保护 409 行内可见)+ 删除受控确认(资源回个人语义文案)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Trash2, Users } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { QueryErrorAlert } from "@/components/query-error";
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
  // 加成员候选(007 用户清单复用)
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
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">团队</h1>
        <Button onClick={() => { setCreating(true); setFormError(""); }}>
          <Plus aria-hidden />
          新建团队
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        团队=资源组+成员角色(负责人/成员/观察者):Bot 与工作流可归属团队共享,
        团队记忆成员共识双路召回;个人资源仅创建者(零回归)。
      </p>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>标识</TableHead>
              <TableHead>名称</TableHead>
              <TableHead>创建时间</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {teams.isError ? (
              <TableRow>
                <TableCell colSpan={4}>
                  <QueryErrorAlert />
                </TableCell>
              </TableRow>
            ) : (teams.data ?? []).length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="py-8 text-center text-muted-foreground">
                  尚未加入任何团队。
                </TableCell>
              </TableRow>
            ) : (
              (teams.data ?? []).map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-mono text-xs">{row.team_key}</TableCell>
                  <TableCell className="max-w-40 truncate" title={row.name}>{row.name}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {row.created_at ? new Date(row.created_at).toLocaleDateString() : ""}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => {
                          setManaging(row);
                          setMemberError("");
                        }}
                      >
                        <Users aria-hidden />
                        成员
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        aria-label={`删除 ${row.name}`}
                        onClick={() => { setDeleting(row); setDeleteError(""); }}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

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
            <div className="space-y-1.5">
              <Label htmlFor="team-key">标识(team_key)</Label>
              <Input
                id="team-key"
                value={form.teamKey}
                onChange={(e) => setForm({ ...form, teamKey: e.target.value })}
                className="font-mono"
                placeholder="小写字母开头"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="team-name">名称</Label>
              <Input
                id="team-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
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
            {detail.isError && <QueryErrorAlert />}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>用户</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(detail.data?.members ?? []).map((m) => (
                  <TableRow key={m.user_id}>
                    <TableCell className="max-w-40 truncate text-sm" title={m.username}>{m.username}</TableCell>
                    <TableCell>
                      <Select
                        value={m.role}
                        onValueChange={(v) => changeRole.mutate({ userId: m.user_id, role: v })}
                      >
                        <SelectTrigger className="h-7 w-28" aria-label={`角色 ${m.username}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="OWNER">负责人</SelectItem>
                          <SelectItem value="MEMBER">成员</SelectItem>
                          <SelectItem value="VIEWER">观察者</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant={memberConfirm === m.user_id ? "destructive" : "ghost"}
                        size="icon-xs"
                        aria-label={`移除 ${m.username}${memberConfirm === m.user_id ? "(再次点击确认)" : ""}`}
                        disabled={removeMember.isPending}
                        onClick={() => {
                          // 审查循环 B6:移除成员补二次确认(首击选中,再击执行)
                          if (memberConfirm === m.user_id) {
                            setMemberConfirm(null);
                            removeMember.mutate(m.user_id);
                          } else {
                            setMemberConfirm(m.user_id);
                          }
                        }}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
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
                          // 036⑧:按 user_id 语义键排除(username 可改会漏排)
                          !(detail.data?.members ?? []).some(
                            (m) => m.user_id === u.id,
                          ),
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
              成员关系解除;归属团队的 Bot/工作流自动回到个人(创建者可见,不丢资源)。
              此操作不可恢复。
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
