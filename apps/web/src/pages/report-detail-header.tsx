/* 报告详情页头:版本切换、编辑/刷新/导出/删除等操作按钮组。 */

import { Download, FileText, RefreshCw, Trash2 } from "lucide-react";

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
} from "@kongming/ui/alert-dialog";
import { Button } from "@kongming/ui/button";
import { PageHeader } from "@kongming/ui/page-header";
import { StatusBadge } from "@kongming/ui/status-badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import {
  exportReport,
  REPORT_PHASE_LABEL,
  REPORT_STATUS_META,
  type ReportDetailOut,
} from "@kongming/api-client/report-api";

import { EditReportDialog } from "./report-edit-dialog";

interface VersionItem {
  version: number;
}

interface ReportHeaderActionsProps {
  detail: ReportDetailOut;
  viewVersion: number | null;
  setViewVersion: (version: number) => void;
  versions: VersionItem[] | undefined;
  /** 生成中不提供版本切换(轮询期详情随终态翻转) */
  generating: boolean;
  editable: boolean;
  isLatest: boolean;
  refresh: { isPending: boolean; mutate: () => void };
  remove: { mutate: () => void; error: Error | null };
  onSaved: () => void;
  onError: (message: string) => void;
}

/** 版本下拉:多于一个版本且不在生成中时出现。 */
function VersionSelect({
  detail,
  viewVersion,
  setViewVersion,
  versions,
}: Pick<ReportHeaderActionsProps, "detail" | "viewVersion" | "setViewVersion" | "versions">) {
  if (!versions || versions.length <= 1) return null;
  return (
    <Select
      value={String(viewVersion ?? detail.report.version)}
      onValueChange={(v) => setViewVersion(Number(v))}
    >
      <SelectTrigger className="h-8 w-36" aria-label="切换版本">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {versions.map((v) => (
          <SelectItem key={v.version} value={String(v.version)}>
            v{v.version}
            {v.version === detail.report.version ? "(最新)" : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** 删除按钮 + 二次确认(不可撤销);失败时保留对话框并给出原因。 */
function DeleteReportButton({
  detail,
  remove,
}: {
  detail: ReportDetailOut;
  remove: { mutate: () => void; error: Error | null };
}) {
  return (
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
            「{detail.report.title}」将不再出现在列表,导出与刷新也被拒;此操作不可撤销。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {remove.error && (
          <p className="text-xs text-destructive" role="alert">
            {remove.error.message}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={() => remove.mutate()}>
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** 页头操作组:版本切换、编辑、刷新数据、导出、删除。 */
function ReportHeaderActions(props: ReportHeaderActionsProps) {
  const { detail: d, viewVersion, editable, isLatest, refresh, generating } = props;
  return (
    <>
      {!generating && (
        <VersionSelect
          detail={d}
          viewVersion={viewVersion}
          setViewVersion={props.setViewVersion}
          versions={props.versions}
        />
      )}
      {editable && isLatest && <EditReportDialog detail={d} onSaved={props.onSaved} />}
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
          void exportReport(d.report.id, viewVersion ?? undefined).catch((e: unknown) =>
            props.onError(e instanceof Error ? e.message : "导出未成"),
          )
        }
      >
        <Download aria-hidden /> 导出
      </Button>
      {isLatest && (d.report.status === "FAILED" || editable) && (
        <DeleteReportButton detail={d} remove={props.remove} />
      )}
    </>
  );
}

interface ReportPageHeaderProps {
  detail: ReportDetailOut;
  progress: { phase?: string | null } | undefined;
  generating: boolean;
  versions: VersionItem[] | undefined;
  viewVersion: number | null;
  setViewVersion: (version: number) => void;
  editable: boolean;
  isLatest: boolean;
  onRefresh: () => void;
  refreshPending: boolean;
  onDelete: () => void;
  deleteError: Error | null;
  onSaved: () => void;
  onError: (message: string) => void;
}

/** 报告详情页头:标题、状态/进度/版本说明与操作组。 */
export function ReportPageHeader(props: ReportPageHeaderProps) {
  const { detail: d, generating } = props;
  return (
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
              {REPORT_PHASE_LABEL[props.progress?.phase ?? d.report.phase] ?? "生成中"}
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
        <ReportHeaderActions
          detail={d}
          viewVersion={props.viewVersion}
          setViewVersion={props.setViewVersion}
          versions={props.versions}
          generating={generating}
          editable={props.editable}
          isLatest={props.isLatest}
          refresh={{ isPending: props.refreshPending, mutate: props.onRefresh }}
          remove={{ mutate: props.onDelete, error: props.deleteError }}
          onSaved={props.onSaved}
          onError={props.onError}
        />
      }
    />
  );
}
