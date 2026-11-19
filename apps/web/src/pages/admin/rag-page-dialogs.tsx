/* 知识库页的编辑、删除、重建与归属变更确认弹窗。 */

import type { UseMutationResult } from "@tanstack/react-query";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { FormRow } from "@/components/form-row";
import type { KnowledgeItemOut, ReindexOut } from "@/lib/rag-api";
import type { TeamItem } from "@/lib/team-api";

type EditVars = { id: string; comment: string; alias: string };
type OwnershipVars = { id: string; teamId: string | null };

const errText = (e: unknown) => (e as Error).message;

// 表说明与业务称谓别名同面对话框编辑;保存后标记 MANUAL_EDIT,自动润色不覆盖
export function RagEditDialog({
  target,
  comment,
  alias,
  onCommentChange,
  onAliasChange,
  onClose,
  edit,
}: {
  target: KnowledgeItemOut | null;
  comment: string;
  alias: string;
  onCommentChange: (v: string) => void;
  onAliasChange: (v: string) => void;
  onClose: () => void;
  edit: UseMutationResult<KnowledgeItemOut, Error, EditVars>;
}) {
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑表说明</DialogTitle>
          <DialogDescription>
            表 {target?.schema_name}.{target?.table_name}
            ——保存后标记为「人工精修」,自动扫描/润色不再覆盖。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <FormRow label="表说明" htmlFor="rag-edit-comment">
            <Textarea
              id="rag-edit-comment"
              value={comment}
              maxLength={500}
              rows={3}
              onChange={(e) => onCommentChange(e.target.value)}
              placeholder="一句话中文表说明"
            />
          </FormRow>
          <FormRow label="业务称谓别名" htmlFor="rag-edit-alias">
            <Input
              id="rag-edit-alias"
              value={alias}
              maxLength={64}
              onChange={(e) => onAliasChange(e.target.value)}
              placeholder="如「商品表」;留空表示无别名"
            />
            <p className="text-xs text-muted-foreground">
              摘要与洞察将以此称谓提及该表(用户用语优先);留空时回退表说明中的中文名称。
            </p>
          </FormRow>
        </div>
        {edit.isError && (
          <p className="text-sm text-destructive" role="alert">
            {errText(edit.error)}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={edit.isPending}
            onClick={() => target && edit.mutate({ id: target.id, comment, alias })}
          >
            {edit.isPending ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// open 只看 target:成功关闭由 onSuccess setDeleteTarget(null) 收口;
// 若并入 !remove.isSuccess,一次成功后 mutation 状态保持 true,
// 后续删除对话框永远打不开(须先 mutate 才重置——而不开框就到不了 mutate)
export function RagDeleteDialog({
  target,
  onClose,
  remove,
}: {
  target: KnowledgeItemOut | null;
  onClose: () => void;
  remove: UseMutationResult<void, Error, string>;
}) {
  return (
    <AlertDialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除知识条目?</AlertDialogTitle>
          <AlertDialogDescription>
            将删除表 {target?.schema_name}.{target?.table_name} 的知识条目, 删除后 AI
            问答不再引用;此操作不可恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {remove.isError && (
          <p className="text-sm text-destructive" role="alert">
            {errText(remove.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => target && remove.mutate(target.id)}
          >
            {remove.isPending ? "删除中…" : "删除"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function RagReindexDialog({
  open,
  onOpenChange,
  actionDs,
  reindex,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  actionDs: string;
  reindex: Omit<UseMutationResult<ReindexOut, Error, string>, "mutate"> & { mutate: () => void };
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>重建索引?</AlertDialogTitle>
          <AlertDialogDescription>
            将清空数据源「{actionDs || "default"}」在该租户的全部表说明并重新扫描 (含 AI
            润色)——人工上传/精修的条目也会被清掉,回到自动生成基线; 团队归属的条目将回到公共可见。
            大库耗时较长,请耐心等待。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={reindex.isPending}
            onClick={() => reindex.mutate()}
          >
            {reindex.isPending ? "重建中…" : "确认重建"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// 归属变更受控确认:明示变更后可见范围变化;取消则不动
export function RagOwnershipDialog({
  target,
  dest,
  onDestChange,
  writableTeams,
  onClose,
  setOwnership,
}: {
  target: KnowledgeItemOut | null;
  dest: string;
  onDestChange: (v: string) => void;
  writableTeams: TeamItem[];
  onClose: () => void;
  setOwnership: UseMutationResult<KnowledgeItemOut, Error, OwnershipVars>;
}) {
  // 目标归属与现状相同则无需变更
  const unchanged = dest === (target?.team_id ?? "public");
  return (
    <AlertDialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>变更归属?</AlertDialogTitle>
          <AlertDialogDescription>
            表 {target?.schema_name}.{target?.table_name}
            ——当前{target?.team_id ? "归属团队专属" : "为租户公共"}。
            变更后可见范围随之变化:团队专属=仅该团队成员在问答中可见;租户公共=全租户可见。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <FormRow label="目标归属" htmlFor="rag-ownership-dest">
          <Select value={dest} onValueChange={onDestChange}>
            <SelectTrigger id="rag-ownership-dest" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="public">租户公共(全租户可见)</SelectItem>
              {writableTeams.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}(仅团队成员可见)
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormRow>
        {setOwnership.isError && (
          <p className="text-sm text-destructive" role="alert">
            {errText(setOwnership.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button
            disabled={setOwnership.isPending || unchanged}
            onClick={() =>
              target &&
              setOwnership.mutate({
                id: target.id,
                teamId: dest === "public" ? null : dest,
              })
            }
          >
            {setOwnership.isPending ? "变更中…" : "确认变更"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
