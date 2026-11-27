/* 展示报告章节和历史版本，并提供刷新、导出、删除和编辑操作；生成时轮询进度。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ErrorAlert } from "@kongming/ui/error-alert";
import { Button } from "@kongming/ui/button";
import { Skeleton } from "@kongming/ui/skeleton";
import { useTenant } from "@kongming/api-client/tenant-store";
import {
  deleteReport,
  getProgress,
  getReport,
  listVersions,
  refreshReport,
} from "@kongming/api-client/report-api";

import { ReportPageHeader } from "./report-detail-header";
import { SectionBlock } from "./report-detail-sections";

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

  /** 详情与版本清单同时失效(刷新、编辑保存与生成终态共用)。 */
  const handleSaved = () => {
    setActionError(null);
    void qc.invalidateQueries({ queryKey: ["report", tenant, reportId] });
    void qc.invalidateQueries({ queryKey: ["report-versions", tenant, reportId] });
  };

  const refresh = useMutation({
    mutationFn: () => refreshReport(reportId),
    onSuccess: handleSaved,
    onError: (e) => setActionError(e instanceof Error ? e.message : "刷新未成"),
  });

  const remove = useMutation({
    mutationFn: () => deleteReport(reportId),
    onSuccess: () => {
      setActionError(null);
      void qc.invalidateQueries({ queryKey: ["reports"] });
      void navigate("/reports");
    },
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

      <ReportPageHeader
        detail={d}
        progress={progress.data}
        generating={generating}
        versions={versions.data}
        viewVersion={viewVersion}
        setViewVersion={setViewVersion}
        editable={editable}
        isLatest={isLatest}
        onRefresh={() => refresh.mutate()}
        refreshPending={refresh.isPending}
        onDelete={() => remove.mutate()}
        deleteError={remove.error}
        onSaved={handleSaved}
        onError={setActionError}
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
