/* 团队页的三个对话框:新建团队、成员管理与删除确认。 */

import { Loader2 } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import type { getTeam, TeamItem } from "@/lib/team-api";

type TeamDetail = Awaited<ReturnType<typeof getTeam>>;

/** 团队成员角色选项(三处下拉共用)。 */
function RoleItems() {
  return (
    <>
      <SelectItem value="OWNER">负责人</SelectItem>
      <SelectItem value="MEMBER">成员</SelectItem>
      <SelectItem value="VIEWER">观察者</SelectItem>
    </>
  );
}

interface MemberManagementDialogProps {
  managing: TeamItem | null;
  detail: { data: TeamDetail | undefined; isError: boolean };
  users: { id: string; username: string }[];
  memberForm: { userId: string; role: string };
  setMemberForm: (form: { userId: string; role: string }) => void;
  memberError: string;
  memberConfirm: string | null;
  onMemberConfirm: (userId: string | null) => void;
  addPending: boolean;
  removePending: boolean;
  onClose: () => void;
  onAdd: () => void;
  onChangeRole: (userId: string, role: string) => void;
  onRemove: (userId: string) => void;
}

/** 成员表:角色下拉 + 首击选中/再击执行的移除。 */
function MemberTable(props: MemberManagementDialogProps) {
  const members = props.detail.data?.members ?? [];
  return (
    <DataView
      rows={members}
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
            <Select value={m.role} onValueChange={(v) => props.onChangeRole(m.user_id, v)}>
              <SelectTrigger size="sm" className="w-28" aria-label={`角色 ${m.username}`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <RoleItems />
              </SelectContent>
            </Select>
          ),
        },
      ]}
      actions={[
        {
          // 移除成员二次确认(首击选中,再击执行),武装态
          // 经行级 label 可视;挂起期仍以 onSelect 守卫兜底
          label: (m) => (props.memberConfirm === m.user_id ? "再次点击确认移除" : "移除"),
          destructive: true,
          onSelect: (m) => {
            if (props.removePending) return;
            if (props.memberConfirm === m.user_id) {
              props.onMemberConfirm(null);
              props.onRemove(m.user_id);
            } else {
              props.onMemberConfirm(m.user_id);
            }
          },
        },
      ]}
    />
  );
}

/** 添加成员表单:候选按不可变 user_id 排除已是成员的用户。 */
function AddMemberForm(props: MemberManagementDialogProps) {
  const members = props.detail.data?.members ?? [];
  const candidates = props.users.filter((u) => !members.some((m) => m.user_id === u.id));
  const { memberForm, setMemberForm } = props;
  return (
    <form
      className="flex items-end gap-2 border-t pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (memberForm.userId) props.onAdd();
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
            {candidates.map((u) => (
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
          <RoleItems />
        </SelectContent>
      </Select>
      <Button type="submit" disabled={props.addPending || !memberForm.userId}>
        {props.addPending && <Loader2 className="animate-spin" aria-hidden />}
        添加
      </Button>
    </form>
  );
}

/** 成员管理对话框。 */
export function MemberManagementDialog(props: MemberManagementDialogProps) {
  return (
    <Dialog open={props.managing !== null} onOpenChange={(o) => !o && props.onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>成员管理「{props.managing?.name}」</DialogTitle>
          <DialogDescription>
            负责人全权/成员可编辑团队资源/观察者可见可运行;末位负责人不可移除。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {props.detail.isError && <ErrorAlert />}
          <MemberTable {...props} />
          <AddMemberForm {...props} />
          {props.memberError && (
            <p role="alert" className="text-sm text-destructive">
              {props.memberError}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

interface CreateTeamDialogProps {
  open: boolean;
  form: { teamKey: string; name: string };
  setForm: (form: { teamKey: string; name: string }) => void;
  error: string;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: () => void;
}

/** 新建团队对话框(创建中不可关闭)。 */
export function CreateTeamDialog(props: CreateTeamDialogProps) {
  const { form, setForm } = props;
  return (
    <Dialog
      open={props.open}
      onOpenChange={(o) => {
        if (props.pending) return;
        props.onOpenChange(o);
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
            props.onSubmit();
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
          {props.error && (
            <p role="alert" className="text-sm text-destructive">
              {props.error}
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={props.pending}>
              {props.pending && <Loader2 className="animate-spin" aria-hidden />}
              创建
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface DeleteTeamDialogProps {
  deleting: TeamItem | null;
  error: string;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

/** 删除团队确认(删除中不可关闭)。 */
export function DeleteTeamDialog(props: DeleteTeamDialogProps) {
  return (
    <AlertDialog
      open={props.deleting !== null}
      onOpenChange={(o) => {
        if (props.pending) return;
        props.onOpenChange(o);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除团队「{props.deleting?.name}」?</AlertDialogTitle>
          <AlertDialogDescription>
            成员关系解除;归属团队的 Bot/工作流自动回到个人(创建者可见,不丢资源)。 此操作不可恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {props.error && (
          <p role="alert" className="text-sm text-destructive">
            {props.error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={props.pending}>再想想</AlertDialogCancel>
          <Button variant="destructive" disabled={props.pending} onClick={props.onConfirm}>
            {props.pending && <Loader2 className="animate-spin" aria-hidden />}
            确认删除
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
