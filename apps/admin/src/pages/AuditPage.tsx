/* 审计页面提供 SQL 审计和操作日志查询；归档操作由 API 和定时任务处理。 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ScrollText } from "lucide-react";

import { PageHeader } from "@kongming/ui/page-header";
import { Pagination } from "@kongming/ui/pagination";
import { ToggleGroup, ToggleGroupItem } from "@kongming/ui/toggle-group";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { operLogPage, sqlAuditPage } from "@kongming/api-client/audit-api";
import { systemApi } from "@kongming/api-client/system-api";
import { endExclusive } from "@kongming/api-client/date-utils";

import { AuditDetailDialog, type AuditDetail } from "./audit-detail-dialog";
import { AuditFiltersBar, type AuditFilters } from "./audit-filters";
import { OperLogTable, SqlAuditTable } from "./audit-tables";

const PAGE_SIZE = 20;
const EMPTY_FILTERS: AuditFilters = {
  traceId: "",
  verdict: "",
  action: "",
  tenantId: "",
  startDate: "",
  endDate: "",
};

/** 审计分区切换(SQL 审计 / 操作日志)。 */
function SectionToggle({
  section,
  onChange,
}: {
  section: "sql" | "oper";
  onChange: (section: "sql" | "oper") => void;
}) {
  return (
    <ToggleGroup
      type="single"
      value={section}
      onValueChange={(v) => {
        if (v) onChange(v as "sql" | "oper");
      }}
      variant="outline"
      size="sm"
      aria-label="审计分区"
    >
      <ToggleGroupItem value="sql" aria-label="SQL 审计">
        <ScrollText aria-hidden /> SQL 审计
      </ToggleGroupItem>
      <ToggleGroupItem value="oper" aria-label="操作日志">
        <ChevronRight aria-hidden /> 操作日志
      </ToggleGroupItem>
    </ToggleGroup>
  );
}

export default function AuditPage() {
  const [section, setSection] = useState<"sql" | "oper">("sql");
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<AuditFilters>(EMPTY_FILTERS);
  // 行详情使用对话框呈现，DataView 不承载行内展开内容。
  const [detail, setDetail] = useState<AuditDetail>(null);
  const { traceId, verdict, action, tenantId, startDate, endDate } = filters;

  const sqlList = useQuery({
    queryKey: ["audit", "sql", page, traceId, verdict, startDate, endDate, tenantId],
    queryFn: () =>
      sqlAuditPage({
        page,
        size: PAGE_SIZE,
        ...(traceId ? { trace_id: traceId } : {}),
        ...(verdict ? { verdict: verdict as "PASS" | "BLOCKED" | "ERROR" } : {}),
        ...(startDate ? { start_time: `${startDate}T00:00:00Z` } : {}),
        ...(endDate ? { end_time: endExclusive(endDate) } : {}),
        ...(tenantId ? { tenant_id: tenantId } : {}),
      }),
    enabled: section === "sql",
  });
  const tenants = useQuery({
    queryKey: ["admin", "tenants"],
    queryFn: systemApi.listTenants,
    enabled: section === "sql",
  });
  const operList = useQuery({
    queryKey: ["audit", "oper", page, action, startDate, endDate],
    queryFn: () =>
      operLogPage({
        page,
        size: PAGE_SIZE,
        ...(action ? { action } : {}),
        ...(startDate ? { start_time: `${startDate}T00:00:00Z` } : {}),
        ...(endDate ? { end_time: endExclusive(endDate) } : {}),
      }),
    enabled: section === "oper",
  });

  const total = section === "sql" ? sqlList.data?.total : operList.data?.total;
  useAuditPageFix({ page, setPage, section, sqlData: sqlList.data, operData: operList.data });

  // 任一条件变化都回到第一页
  const changeFilters = (patch: Partial<AuditFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="审计"
        description="谁问了什么、谁改过什么——SQL 审计与操作日志的可视化台账。"
        actions={<SectionToggle section={section} onChange={setSection} />}
      />

      <AuditFiltersBar
        section={section}
        filters={filters}
        onChange={changeFilters}
        tenants={tenants.data}
        tenantsError={tenants.isError}
      />

      {/* 查询失败:错误条置于表区上方(UsersPage 同口径),DataView 只管加载/空态 */}
      {section === "sql" && sqlList.isError && <ErrorAlert />}
      {section === "oper" && operList.isError && <ErrorAlert />}

      {section === "sql" ? (
        <SqlAuditTable
          items={sqlList.data?.items ?? []}
          isPending={sqlList.isPending}
          onDetail={(item) => setDetail({ kind: "sql", item })}
        />
      ) : (
        <OperLogTable
          items={operList.data?.items ?? []}
          isPending={operList.isPending}
          onDetail={(item) => setDetail({ kind: "oper", item })}
        />
      )}

      <Pagination
        page={page}
        pageCount={Math.max(1, Math.ceil((total ?? 0) / PAGE_SIZE))}
        total={total ?? 0}
        onPageChange={setPage}
      />

      <AuditDetailDialog detail={detail} onClose={() => setDetail(null)} />
    </div>
  );
}

/**
 * 分页修正:切换审计类型回到第一页;当前页被清空时退回一页,避免空页。
 */
function useAuditPageFix(fix: {
  page: number;
  setPage: React.Dispatch<React.SetStateAction<number>>;
  section: "sql" | "oper";
  sqlData: { total?: number; items: unknown[] } | undefined;
  operData: { total?: number; items: unknown[] } | undefined;
}): void {
  const { page, setPage, section, sqlData, operData } = fix;
  useEffect(() => {
    setPage(1);
  }, [section, setPage]);
  useEffect(() => {
    const t = section === "sql" ? sqlData?.total : operData?.total;
    const items = section === "sql" ? sqlData?.items : operData?.items;
    if (t !== undefined && t > 0 && page > 1 && items?.length === 0) {
      setPage((p) => p - 1);
    }
  }, [sqlData, operData, page, section, setPage]);
}
