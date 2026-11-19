/* 报告编辑对话框:标题 + 各章节标题/叙述(markdown)；提交仅传变更节,乐观锁随 base_version。 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Pencil } from "lucide-react";

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
import { Textarea } from "@/components/ui/textarea";
import { saveEdit, type ReportDetailOut, type SectionOut } from "@/lib/report-api";

type SectionEdit = { title?: string; narrative_md?: string };

/** 章节缺数/未完成时在标题标签后追加的状态后缀。 */
function sectionStatusSuffix(s: SectionOut): string {
  if (s.data_status === "MISSING") return "(缺数)";
  if (s.data_status === "PENDING") return "(未完成)";
  return "";
}

/** 叙述输入的行数:按内容行数增长,3–12 行之间。 */
function narrativeRows(text: string): number {
  return Math.min(Math.max(text.split("\n").length + 1, 3), 12);
}

/** 单章节编辑区(标题 + 叙述)。 */
function SectionEditor({
  s,
  edits,
  onChange,
}: {
  s: SectionOut;
  edits: SectionEdit | undefined;
  onChange: (patch: SectionEdit) => void;
}) {
  const narrative = edits?.narrative_md ?? s.narrative_md;
  return (
    <div className="space-y-1 rounded-lg border border-border p-3">
      <Label htmlFor={`sec-title-${s.id}`} className="text-xs text-muted-foreground">
        章节标题
        {sectionStatusSuffix(s)}
      </Label>
      <Input
        id={`sec-title-${s.id}`}
        value={edits?.title ?? s.title}
        onChange={(e) => onChange({ title: e.target.value })}
        maxLength={200}
      />
      <Label htmlFor={`sec-narr-${s.id}`} className="text-xs text-muted-foreground">
        叙述(markdown)
      </Label>
      <Textarea
        id={`sec-narr-${s.id}`}
        value={narrative}
        onChange={(e) => onChange({ narrative_md: e.target.value })}
        rows={narrativeRows(narrative)}
      />
    </div>
  );
}

/** 编辑入口按钮 + 编辑对话框(保存后产生新版本)。 */
export function EditReportDialog({
  detail,
  onSaved,
}: {
  detail: ReportDetailOut;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(detail.report.title);
  const [edits, setEdits] = useState<Record<string, SectionEdit>>({});
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      saveEdit(detail.report.id, {
        base_version: detail.report.version,
        title,
        sections: Object.entries(edits).map(([section_id, v]) => ({ section_id, ...v })),
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onSaved();
    },
    onError: (e) => setError(e instanceof Error ? e.message : "保存未成"),
  });

  function setSection(id: string, patch: SectionEdit): void {
    setEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          setTitle(detail.report.title);
          setEdits({});
          setError(null);
        }
      }}
    >
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} aria-label="编辑报告">
        <Pencil aria-hidden /> 编辑
      </Button>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>编辑报告(产生新版本)</DialogTitle>
          <DialogDescription>
            仅标题与叙述可改;数据绑定随版本只读,数据更新走「刷新数据」。基于 v
            {detail.report.version} 编辑,他人先保存会冲突拒绝。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="report-edit-title">报告标题</Label>
            <Input
              id="report-edit-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
            />
          </div>
          {detail.sections.map((s) => (
            <SectionEditor
              key={s.id}
              s={s}
              edits={edits[s.id]}
              onChange={(patch) => setSection(s.id, patch)}
            />
          ))}
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            取消
          </Button>
          <Button onClick={() => save.mutate()} disabled={save.isPending}>
            保存(产生 v{detail.report.version + 1})
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
