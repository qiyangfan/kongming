/* 报告列表使用 DataView 展示状态和分页；行操作打开报告详情。 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { DataView, type DataViewColumn } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { useTenant } from "@/lib/tenant-store";
import { listReports, REPORT_PHASE_LABEL, REPORT_STATUS_META } from "@/lib/report-api";
import type { ReportItem } from "@/lib/report-api";

const PAGE_SIZE = 20;
const REPORTS_POLL_MS = 2000;

export default function ReportsPage() {
  const navigate = useNavigate();
  const tenant = useTenant((s) => s.current);
  const [page, setPage] = useState(1);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["reports", tenant, page],
    queryFn: () => listReports(page, PAGE_SIZE),
    // 存在生成中报告时轮询刷新,全终态即停
    refetchInterval: (q) =>
      (q.state.data?.items ?? []).some((r) => r.status === "GENERATING") ? REPORTS_POLL_MS : false,
  });

  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // 末页行被清空时钳回有效页(否则滞留空页且分页器已消失,无入口返回)
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const columns: DataViewColumn<ReportItem>[] = [
    {
      key: "title",
      header: "标题",
      cardPriority: 1,
      render: (r) => <span className="font-medium text-foreground">{r.title}</span>,
    },
    {
      key: "status",
      header: "状态",
      cardPriority: 2,
      render: (r) => <StatusBadge meta={REPORT_STATUS_META} value={r.status} />,
    },
    {
      key: "phase",
      header: "进度",
      render: (r) => (r.status === "GENERATING" ? (REPORT_PHASE_LABEL[r.phase] ?? r.phase) : "—"),
    },
    { key: "version", header: "版本", render: (r) => `v${r.version}` },
    {
      key: "updated_at",
      header: "更新时间",
      render: (r) => new Date(r.updated_at).toLocaleString(),
    },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-4 py-6">
      <PageHeader title="分析报告" description="由问答与指令生成的可保存、可版本化的分析交付物" />
      {isError && <ErrorAlert />}
      <DataView
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(r) => r.id}
        isLoading={isLoading}
        isEmpty={!isLoading && !isError && total === 0}
        emptyText="还没有报告。在对话中点击「生成报告」即可创建第一份。"
        actions={[
          {
            label: "查看",
            onSelect: (r) => void navigate(`/reports/${r.id}`),
          },
        ]}
      />
      {total > PAGE_SIZE && (
        <Pagination page={page} pageCount={pageCount} total={total} onPageChange={setPage} />
      )}
    </div>
  );
}
