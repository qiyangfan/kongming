/* 查看已注册工具和执行审计；需要 system:tool:manage 权限。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { ErrorAlert } from "@/components/error-alert";
import { toolAuditPage, toolList } from "@/lib/toolcall-api";
import { endExclusive } from "@/lib/date-utils";

import { usePageFallback } from "./admin-filter-bits";
import { ToolsAuditFilters } from "./tools-audit-filters";
import { ToolAuditTable, ToolRegistryTable } from "./tools-tables";

const PAGE_SIZE = 20;

/** 执行状态筛选项(与审计记录的 status 取值一致)。 */
const STATUS_OPTIONS = [
  { value: "EXECUTED", label: "已执行" },
  { value: "REJECTED", label: "已拒绝" },
  { value: "ERROR", label: "失败" },
  { value: "RATE_LIMITED", label: "已限流" },
  { value: "PENDING_CONFIRM", label: "待确认" },
  { value: "CONFIRM_REJECTED", label: "已拒绝确认" },
  { value: "CONFIRM_EXPIRED", label: "确认过期" },
];

export default function ToolsPage() {
  const [page, setPage] = useState(1);
  const [toolName, setToolName] = useState("");
  const [status, setStatus] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const tools = useQuery({ queryKey: ["toolcall", "registry"], queryFn: toolList });
  const audit = useQuery({
    queryKey: ["toolcall", "audit", page, toolName, status, startDate, endDate],
    queryFn: () =>
      toolAuditPage({
        page,
        size: PAGE_SIZE,
        ...(toolName ? { tool_name: toolName } : {}),
        ...(status ? { status } : {}),
        ...(startDate ? { start: `${startDate}T00:00:00Z` } : {}),
        ...(endDate ? { end: endExclusive(endDate) } : {}),
      }),
  });

  const total = audit.data?.total ?? 0;
  usePageFallback(audit.data, page, setPage);

  return (
    <div className="space-y-4">
      <PageHeader title="工具" description="AI 可使用的内置工具清单,以及每次工具执行的审计记录。" />

      {/* 注册表清单(只读) */}
      {tools.isError && <ErrorAlert />}
      <ToolRegistryTable tools={tools.data?.tools ?? []} isPending={tools.isPending} />

      {/* 执行审计 */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">执行审计</h3>
        <ToolsAuditFilters
          toolNames={(tools.data?.tools ?? []).map((t) => t.name)}
          statusOptions={STATUS_OPTIONS}
          toolName={toolName}
          status={status}
          startDate={startDate}
          endDate={endDate}
          onToolName={(v) => {
            setToolName(v);
            setPage(1);
          }}
          onStatus={(v) => {
            setStatus(v);
            setPage(1);
          }}
          onDateRange={(s, e) => {
            setStartDate(s);
            setEndDate(e);
            setPage(1);
          }}
        />

        {audit.isError && <ErrorAlert />}
        <ToolAuditTable items={audit.data?.items ?? []} isPending={audit.isPending} />

        <Pagination
          page={page}
          pageCount={Math.max(1, Math.ceil(total / PAGE_SIZE))}
          total={total}
          onPageChange={setPage}
        />
      </div>
    </div>
  );
}
