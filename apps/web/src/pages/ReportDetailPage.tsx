/* 展示报告章节和历史版本，并提供刷新、导出、删除和编辑操作；生成时轮询进度。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, FileText, Pencil, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ChartView } from "@/components/chart-view";
import type { ChartSpec } from "@/components/chart-shared";
import { ErrorAlert } from "@/components/error-alert";
import { MarkdownText } from "@/components/markdown-text";
import { PageHeader } from "@/components/page-header";
import { ResultTable } from "@/components/result-table";
import { StatusBadge } from "@/components/status-badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
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
import { useTenant } from "@/lib/tenant-store";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  deleteReport,
  exportReport,
  getProgress,
  getReport,
  listVersions,
  refreshReport,
  REPORT_PHASE_LABEL,
  REPORT_STATUS_META,
  saveEdit,
  type ReportBinding,
  type ReportDetailOut,
  type SectionOut,
} from "@/lib/report-api";

export default function ReportDetailPage() {
  const { reportId = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const tenant = useTenant((s) => s.current);
  const [viewVersion, setViewVersion] = useState<number | null>(null); // null=最新
  const [actionError, setActionError] = useState<string | null>(null);

  const detail = useQuery({
    queryKey: ["report", tenant, reportId, viewVersion],
    queryFn: () => getReport(reportId, viewVersion ?? undefined),
  });

  const status = detail.data?.report.status;
  const generating = status === "GENERATING";
  const progress = useQuery({
    queryKey: ["report-progress", tenant, reportId],
    queryFn: () => getProgress(reportId),
    enabled: generating,
    refetchInterval: 2000,
  });
  // 终态翻转即失效详情(轮询侧不再 enabled,详情回落权威态;副作用入 useEffect)
  useEffect(() => {
    if (generating && progress.data && progress.data.status !== "GENERATING") {
      void qc.invalidateQueries({ queryKey: ["report", tenant, reportId] });
    }
  }, [generating, progress.data, qc, tenant, reportId]);

  const versions = useQuery({
    queryKey: ["report-versions", tenant, reportId],
    queryFn: () => listVersions(reportId),
    enabled: !generating && status !== undefined && status !== "FAILED",
  });

  const refresh = useMutation({
    mutationFn: () => refreshReport(reportId),
    onSuccess: () => {
      setActionError(null);
      void qc.invalidateQueries({ queryKey: ["report", tenant, reportId] });
      void qc.invalidateQueries({ queryKey: ["report-versions", tenant, reportId] });
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "刷新未成"),
  });

  const remove = useMutation({
    mutationFn: () => deleteReport(reportId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reports"] });
      void navigate("/reports");
    },
    onError: (e) => setActionError(e instanceof Error ? e.message : "删除未成"),
  });

  if (detail.isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-3 px-4 py-6">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-6">
        <ErrorAlert text="报告不存在或不可见(可能已被删除)。" />
        <Button asChild variant="ghost" size="sm" className="mt-2">
          <Link to="/reports">
            <ArrowLeft aria-hidden /> 返回列表
          </Link>
        </Button>
      </div>
    );
  }

  const d = detail.data;
  const editable = d.report.status === "READY" || d.report.status === "PARTIAL";
  const isLatest = viewVersion === null || viewVersion === d.report.version;

  return (
    <div className="mx-auto max-w-4xl space-y-5 px-4 py-6">
      <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
        <Link to="/reports">
          <ArrowLeft aria-hidden /> 报告列表
        </Link>
      </Button>

      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <FileText className="size-5 text-muted-foreground" aria-hidden />
            {d.report.title}
          </span>
        }
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge meta={REPORT_STATUS_META} value={d.report.status} />
            {generating && (
              <span className="text-xs text-muted-foreground">
                {REPORT_PHASE_LABEL[progress.data?.phase ?? d.report.phase] ?? "生成中"}
                <span className="ml-1 inline-block animate-pulse">…</span>
              </span>
            )}
            <span className="text-xs text-muted-foreground">
              版本 v{d.report.version} · 数据时点{" "}
              {d.version_meta ? new Date(d.version_meta.data_time_point).toLocaleString() : "—"}
            </span>
          </span>
        }
        actions={
          <>
            {!generating && versions.data && versions.data.length > 1 && (
              <Select
                value={String(viewVersion ?? d.report.version)}
                onValueChange={(v) => setViewVersion(Number(v))}
              >
                <SelectTrigger className="h-8 w-36" aria-label="切换版本">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {versions.data.map((v) => (
                    <SelectItem key={v.version} value={String(v.version)}>
                      v{v.version}
                      {v.version === d.report.version ? "(最新)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {editable && isLatest && (
              <EditReportDialog
                detail={d}
                onSaved={() => {
                  setActionError(null);
                  void qc.invalidateQueries({ queryKey: ["report", tenant, reportId] });
                  void qc.invalidateQueries({ queryKey: ["report-versions", tenant, reportId] });
                }}
              />
            )}
            {editable && isLatest && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => refresh.mutate()}
                disabled={refresh.isPending}
              >
                <RefreshCw className={refresh.isPending ? "animate-spin" : ""} aria-hidden />
                刷新数据
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={!d.version_meta}
              onClick={() =>
                void exportReport(reportId, viewVersion ?? undefined).catch((e: unknown) =>
                  setActionError(e instanceof Error ? e.message : "导出未成"),
                )
              }
            >
              <Download aria-hidden /> 导出
            </Button>
            {isLatest && (d.report.status === "FAILED" || editable) && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="ghost" size="sm" className="text-destructive">
                    <Trash2 aria-hidden /> 删除
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>删除这份报告?</AlertDialogTitle>
                    <AlertDialogDescription>
                      「{d.report.title}」将不再出现在列表,导出与刷新也被拒;此操作不可撤销。
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction variant="destructive" onClick={() => remove.mutate()}>
                      删除
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </>
        }
      />

      {actionError && <ErrorAlert text={actionError} />}
      {d.fail_reason && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
          失败原因:{d.fail_reason}
        </p>
      )}
      {!generating && !d.version_meta && (
        <p className="text-sm text-muted-foreground">生成未产出章节(见失败原因),无版本可看。</p>
      )}

      {d.sections.length > 0 && (
        <div className="space-y-6">
          {d.sections.map((s) => (
            <SectionBlock key={s.id} section={s} depth={s.parent_id ? 1 : 0} />
          ))}
        </div>
      )}
    </div>
  );
}

function _binding(b: { [key: string]: unknown }): ReportBinding {
  return {
    sql: String(b.sql ?? ""),
    status: String(b.status ?? "OK"),
    columns: Array.isArray(b.columns) ? (b.columns as string[]) : [],
    rows: Array.isArray(b.rows) ? (b.rows as unknown[][]) : [],
    row_count: Number(b.row_count ?? 0),
    truncated: Boolean(b.truncated),
    fail_reason: (b.fail_reason as string | null) ?? null,
  };
}

function SectionBlock({ section: s, depth }: { section: SectionOut; depth: 0 | 1 }) {
  const heading =
    depth === 0 ? (
      <h3 className="text-lg font-semibold text-foreground">{s.title}</h3>
    ) : (
      <h4 className="text-base font-semibold text-foreground">{s.title}</h4>
    );
  return (
    <section className="space-y-2" aria-label={s.title}>
      {heading}
      {s.data_status === "MISSING" && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
          数据缺失:{s.fail_reason ?? "执行失败"}
        </p>
      )}
      {s.data_status === "PENDING" && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
          未完成:{s.fail_reason ?? "预算耗尽或生成中断"}
        </p>
      )}
      {s.narrative_md && <MarkdownText text={s.narrative_md} />}
      {s.bindings.map((raw, i) => {
        const b = _binding(raw);
        return (
          <div key={i} className="space-y-1">
            {b.status === "OK" ? (
              <>
                {b.columns.length > 0 && <ResultTable columns={b.columns} rows={b.rows} />}
                {b.truncated && (
                  <p className="text-xs text-muted-foreground">
                    结果截断:共 {b.row_count} 行,快照保留前 {b.rows.length} 行。
                  </p>
                )}
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                绑定失败:{b.fail_reason ?? "执行失败"}
              </p>
            )}
          </div>
        );
      })}
      {s.chart && s.bindings.some((b) => b.status === "OK") && <SectionChart section={s} />}
    </section>
  );
}

function SectionChart({ section: s }: { section: SectionOut }) {
  const chart = s.chart as { type?: string; x?: string; y?: string } | null;
  const ok = s.bindings.map(_binding).find((b) => b.status === "OK");
  if (!chart?.type || !chart.x || !chart.y || !ok) return null;
  const candidate: ChartSpec = { type: chart.type, x: chart.x, y: [chart.y], series: null };
  return (
    <div className="rounded-lg border border-border bg-card px-2 py-1">
      <ChartView candidates={[candidate]} data={{ columns: ok.columns, rows: ok.rows }} />
    </div>
  );
}

/** 编辑对话框:标题 + 各章节标题/叙述(markdown);提交仅传变更节,乐观锁随 base_version。 */
function EditReportDialog({ detail, onSaved }: { detail: ReportDetailOut; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(detail.report.title);
  const [edits, setEdits] = useState<Record<string, { title?: string; narrative_md?: string }>>({});
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

  function setSection(id: string, patch: { title?: string; narrative_md?: string }): void {
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
            <div key={s.id} className="space-y-1 rounded-lg border border-border p-3">
              <Label htmlFor={`sec-title-${s.id}`} className="text-xs text-muted-foreground">
                章节标题
                {s.data_status === "MISSING"
                  ? "(缺数)"
                  : s.data_status === "PENDING"
                    ? "(未完成)"
                    : ""}
              </Label>
              <Input
                id={`sec-title-${s.id}`}
                value={edits[s.id]?.title ?? s.title}
                onChange={(e) => setSection(s.id, { title: e.target.value })}
                maxLength={200}
              />
              <Label htmlFor={`sec-narr-${s.id}`} className="text-xs text-muted-foreground">
                叙述(markdown)
              </Label>
              <Textarea
                id={`sec-narr-${s.id}`}
                value={edits[s.id]?.narrative_md ?? s.narrative_md}
                onChange={(e) => setSection(s.id, { narrative_md: e.target.value })}
                rows={Math.min(
                  Math.max((edits[s.id]?.narrative_md ?? s.narrative_md).split("\n").length + 1, 3),
                  12,
                )}
              />
            </div>
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
