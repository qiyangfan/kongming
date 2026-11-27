/* 记忆页共用部件:状态徽标映射、分类中文、记忆编辑与删除对话框。 */

import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@kongming/ui/alert-dialog";
import { Button } from "@kongming/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@kongming/ui/dialog";
import { Input } from "@kongming/ui/input";
import { FormRow } from "@kongming/ui/form-row";
import { StatusBadge, type StatusMeta } from "@kongming/ui/status-badge";
import { Badge } from "@kongming/ui/badge";
import type { DataViewColumn } from "@kongming/ui/data-view";
import { api } from "@kongming/api-client/api";
import { formatDate } from "@kongming/api-client/datetime";
import type { components } from "@kongming/api-client/api-types.gen";

export type MemoryItem = components["schemas"]["MemoryItemOut"];

// 记忆来源状态统一使用 StatusBadge。
const SOURCE_META: StatusMeta = {
  EXPLICIT: { label: "显式", variant: "default" },
  IMPLICIT: { label: "隐式", variant: "secondary" },
};
const DORMANT_META: StatusMeta = {
  ACTIVE: { label: "活跃", variant: "secondary" },
  DORMANT: { label: "休眠", variant: "outline" },
};

// 分类中文(后端现为 free-form,内置仅 general;未收录值回退原值)
const CATEGORY_LABELS: Record<string, string> = {
  general: "通用",
};

/** 分类码转中文;未收录值原样回显。 */
function categoryLabel(c: string): string {
  return CATEGORY_LABELS[c] ?? c;
}

interface EditMemoryDialogProps {
  editing: MemoryItem | null;
  onClose: () => void;
  content: string;
  setContent: (value: string) => void;
  category: string;
  setCategory: (value: string) => void;
  error: string | null;
  pending: boolean;
  onSave: () => void;
}

/** 编辑记忆对话框:保存即视为确认,隐式记忆转为已认可。 */
export function EditMemoryDialog(props: EditMemoryDialogProps) {
  const { editing, onClose, pending } = props;
  return (
    <Dialog open={editing !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑记忆</DialogTitle>
          <DialogDescription>保存即视为确认,这条隐式记忆将转为「已认可」。</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <FormRow label="内容" htmlFor="mem-content">
            <Input
              id="mem-content"
              value={props.content}
              onChange={(e) => props.setContent(e.target.value)}
              maxLength={500}
            />
          </FormRow>
          <FormRow label="分类" htmlFor="mem-category">
            <Input
              id="mem-category"
              value={props.category}
              onChange={(e) => props.setCategory(e.target.value)}
              maxLength={32}
            />
          </FormRow>
          {props.error && (
            <p className="text-xs text-destructive" role="alert">
              {props.error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button disabled={!props.content.trim() || pending} onClick={props.onSave}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface RemoveMemoryDialogProps {
  removing: MemoryItem | null;
  onClose: () => void;
  pending: boolean;
  onConfirm: (id: string) => void;
}

/** 删除记忆确认对话框(不可恢复)。 */
export function RemoveMemoryDialog({
  removing,
  onClose,
  pending,
  onConfirm,
}: RemoveMemoryDialogProps) {
  return (
    <AlertDialog open={removing !== null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除这条记忆?</AlertDialogTitle>
          <AlertDialogDescription>
            「{removing?.content}」删除后不可恢复,孔明后续回答不再参考这条记忆。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>再想想</AlertDialogCancel>
          <AlertDialogAction disabled={pending} onClick={() => removing && onConfirm(removing.id)}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** 记忆表列定义:团队名经 teamName 回显,来源/休眠态走 StatusBadge。 */
export function memoryColumns(
  teamName: (id: string | null | undefined) => string | null,
): DataViewColumn<MemoryItem>[] {
  return [
    {
      key: "content",
      header: "内容",
      cardPriority: 1,
      render: (m) => (
        <span className="block max-w-md truncate text-sm" title={m.content}>
          {m.content}
        </span>
      ),
    },
    {
      key: "category",
      header: "分类",
      cellClassName: "text-xs",
      render: (m) => categoryLabel(m.category),
    },
    {
      key: "source",
      header: "来源",
      cardPriority: 2,
      // source 契约为宽 string:未知值回落 secondary
      render: (m) => (
        <StatusBadge meta={SOURCE_META} value={m.source} fallbackVariant="secondary" />
      ),
    },
    {
      key: "team_id",
      header: "归属",
      cardPriority: 4,
      render: (m) =>
        m.team_id ? (
          <Badge variant="outline" className="max-w-32" title={teamName(m.team_id) ?? undefined}>
            <span className="truncate">{teamName(m.team_id)}</span>
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">个人</span>
        ),
    },
    {
      key: "confidence",
      header: "置信度",
      cellClassName: "text-xs",
      render: (m) => `${Math.round(m.confidence * 100)}%`,
    },
    {
      key: "dormant",
      header: "状态",
      cardPriority: 3,
      render: (m) => <StatusBadge meta={DORMANT_META} value={m.dormant ? "DORMANT" : "ACTIVE"} />,
    },
  ];
}

/** 记忆导出:序列化为 JSON 文件并触发下载;失败经 onError 呈现。 */
export async function exportMemories(onError: (message: string) => void): Promise<void> {
  try {
    const data = await api.get<object[]>("/memory/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `我的记忆-${formatDate(new Date())}.json`;
    document.body.appendChild(a); // Safari 纪律:锚点入 DOM+延迟 revoke
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    onError(e instanceof Error ? e.message : "导出失败");
  }
}
