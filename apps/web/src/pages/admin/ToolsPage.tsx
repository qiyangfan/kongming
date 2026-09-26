/* 工具页(019,tool-calling spec「工具管理与审计查询」):已注册工具清单
   (只读)+ 工具执行审计分页(筛选:工具名/状态/时间范围;时间本地化)。
   权限点 system:tool:manage。 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { DataView } from "@/components/data-view";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorAlert } from "@/components/error-alert";
import { DateRangeQuick } from "@/components/date-range-quick";
import { toolAuditPage, toolList } from "@/lib/toolcall-api";
import { endExclusive } from "@/lib/date-utils";
import { formatDateTime, formatDuration } from "@/lib/datetime";

const PAGE_SIZE = 20;

const STATUS_META: StatusMeta = {
  EXECUTED: { label: "已执行", variant: "default" },
  REJECTED: { label: "已拒绝", variant: "secondary" },
  ERROR: { label: "失败", variant: "destructive" },
  RATE_LIMITED: { label: "已限流", variant: "outline" },
  PENDING_CONFIRM: { label: "待确认", variant: "outline" },
  CONFIRM_REJECTED: { label: "已拒绝确认", variant: "secondary" },
  CONFIRM_EXPIRED: { label: "确认过期", variant: "secondary" },
};

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

  const totalPages = Math.max(1, Math.ceil((audit.data?.total ?? 0) / PAGE_SIZE));

  // 末页越界自愈(007/009 范式)
  useEffect(() => {
    const t = audit.data?.total;
    if (t !== undefined && t > 0 && page > 1 && audit.data?.items.length === 0) {
      setPage((p) => p - 1);
    }
  }, [audit.data, page]);

  return (
    <div className="space-y-4">
      <PageHeader title="工具" description="AI 可使用的内置工具清单,以及每次工具执行的审计记录。" />

      {/* 注册表清单(只读) */}
      {tools.isError && <ErrorAlert />}
      <DataView
        rows={tools.data?.tools ?? []}
        rowKey={(t) => t.name}
        isLoading={tools.isPending}
        isEmpty={!tools.isPending && tools.data?.tools.length === 0}
        emptyText="暂无注册工具"
        columns={[
          {
            key: "name",
            header: "工具",
            cardPriority: 1,
            cellClassName: "font-mono text-xs font-medium",
            render: (t) => (
              <span className="flex items-center gap-1.5 font-mono text-xs font-medium">
                <Wrench className="size-3.5 text-muted-foreground" aria-hidden />
                {t.name}
              </span>
            ),
          },
          {
            key: "description",
            header: "描述",
            cardPriority: 3,
            cellClassName: "text-sm text-muted-foreground",
            render: (t) => (
              <span className="block max-w-md truncate" title={t.description}>
                {t.description}
              </span>
            ),
          },
          {
            key: "perm_code",
            header: "权限点",
            cellClassName: "font-mono text-xs",
          },
          {
            key: "write",
            header: "属性",
            cardPriority: 2,
            render: (t) => (
              <span className="flex gap-1">
                <Badge variant={t.write ? "destructive" : "secondary"}>
                  {t.write ? "写" : "只读"}
                </Badge>
                {t.require_confirm && <Badge variant="outline">需确认</Badge>}
              </span>
            ),
          },
        ]}
      />

      {/* 执行审计 */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">执行审计</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="flt-tool" className="text-xs">
              工具
            </Label>
            <Select
              value={toolName || "__all__"}
              onValueChange={(v) => {
                setToolName(v === "__all__" ? "" : v);
                setPage(1);
              }}
            >
              <SelectTrigger id="flt-tool" className="w-52" aria-label="按工具筛选">
                <SelectValue placeholder="全部工具" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部工具</SelectItem>
                {tools.data?.tools.map((t) => (
                  <SelectItem key={t.name} value={t.name}>
                    {t.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="flt-status" className="text-xs">
              状态
            </Label>
            <Select
              value={status || "__all__"}
              onValueChange={(v) => {
                setStatus(v === "__all__" ? "" : v);
                setPage(1);
              }}
            >
              <SelectTrigger id="flt-status" className="w-40" aria-label="按状态筛选">
                <SelectValue placeholder="全部状态" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部状态</SelectItem>
                {Object.entries(STATUS_META).map(([k, m]) => (
                  <SelectItem key={k} value={k}>
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="flt-start" className="text-xs">
              开始日期
            </Label>
            <Input
              id="flt-start"
              type="date"
              value={startDate}
              onChange={(e) => {
                setStartDate(e.target.value);
                setPage(1);
              }}
              className="w-40"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="flt-end" className="text-xs">
              结束日期
            </Label>
            <Input
              id="flt-end"
              type="date"
              value={endDate}
              onChange={(e) => {
                setEndDate(e.target.value);
                setPage(1);
              }}
              className="w-40"
            />
          </div>
          <DateRangeQuick
            onPick={(s, e) => {
              setStartDate(s);
              setEndDate(e);
              setPage(1);
            }}
          />
        </div>

        {audit.isError && <ErrorAlert />}
        <DataView
          rows={audit.data?.items ?? []}
          rowKey={(it) => it.id}
          isLoading={audit.isPending}
          isEmpty={!audit.isPending && audit.data?.items.length === 0}
          emptyText="暂无工具执行记录"
          columns={[
            {
              key: "created_at",
              header: "时间",
              cardPriority: 2,
              cellClassName: "text-xs text-muted-foreground",
              render: (it) => formatDateTime(it.created_at, { seconds: true }),
            },
            {
              key: "tool_name",
              header: "工具",
              cardPriority: 1,
              cellClassName: "font-mono text-xs",
              render: (it) => <span className="font-mono text-xs">{it.tool_name}</span>,
            },
            {
              key: "status",
              header: "状态",
              cardPriority: 3,
              render: (it) => <StatusBadge meta={STATUS_META} value={it.status} />,
            },
            {
              key: "args_digest",
              header: "参数摘要",
              cellClassName: "text-xs text-muted-foreground",
              render: (it) => (
                <span className="block max-w-72 truncate" title={it.args_digest || undefined}>
                  {it.args_digest || "—"}
                </span>
              ),
            },
            {
              key: "duration_ms",
              header: "耗时",
              cardPriority: 5,
              cellClassName: "text-xs",
              render: (it) => formatDuration(it.duration_ms ?? null),
            },
            {
              key: "datasource_key",
              header: "数据源",
              cardPriority: 4,
              cellClassName: "text-xs",
              render: (it) => it.datasource_key ?? "—",
            },
          ]}
        />

        <Pagination
          page={page}
          pageCount={totalPages}
          total={audit.data?.total ?? 0}
          onPageChange={setPage}
        />
      </div>
    </div>
  );
}
