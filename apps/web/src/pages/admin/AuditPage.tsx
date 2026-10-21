/* 审计页面提供 SQL 审计和操作日志查询；归档操作由 API 和定时任务处理。 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, ScrollText } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { DateRangeQuick } from "@/components/date-range-quick";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { operLogPage, sqlAuditPage } from "@/lib/audit-api";
import { systemApi } from "@/lib/system-api";
import { endExclusive } from "@/lib/date-utils";
import { displayUser } from "@/lib/display-user";
import { formatDateTime, formatDuration } from "@/lib/datetime";

const PAGE_SIZE = 20;

const VERDICT_META: StatusMeta = {
  PASS: { label: "通过", variant: "default" },
  BLOCKED: { label: "拦截", variant: "secondary" },
  ERROR: { label: "失败", variant: "outline" },
};

export default function AuditPage() {
  const [section, setSection] = useState<"sql" | "oper">("sql");
  const [page, setPage] = useState(1);
  const [traceId, setTraceId] = useState("");
  const [verdict, setVerdict] = useState("");
  const [action, setAction] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  // 行详情使用对话框呈现，DataView 不承载行内展开内容。
  const [detail, setDetail] = useState<
    { kind: "sql"; item: SqlItem } | { kind: "oper"; item: OperItem } | null
  >(null);

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
  const totalPages = Math.max(1, Math.ceil((total ?? 0) / PAGE_SIZE));

  // 切换审计类型时重置分页;当前页被清空时退回一页,避免空页。
  useEffect(() => {
    setPage(1);
  }, [section]);
  useEffect(() => {
    const t = section === "sql" ? sqlList.data?.total : operList.data?.total;
    if (
      t !== undefined &&
      t > 0 &&
      page > 1 &&
      (section === "sql" ? sqlList.data?.items.length : operList.data?.items.length) === 0
    ) {
      setPage((p) => p - 1);
    }
  }, [sqlList.data, operList.data, page, section]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="审计"
        description="谁问了什么、谁改过什么——SQL 审计与操作日志的可视化台账。"
        actions={
          <ToggleGroup
            type="single"
            value={section}
            onValueChange={(v) => {
              if (v) setSection(v as "sql" | "oper");
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
        }
      />

      {/* 筛选区 */}
      <div className="flex flex-wrap items-end gap-3">
        {section === "sql" ? (
          <>
            <div className="space-y-1">
              <Label htmlFor="audit-tenant-filter" className="text-xs text-muted-foreground">
                租户
              </Label>
              <Select
                value={tenantId || "ALL"}
                onValueChange={(v) => {
                  setTenantId(v === "ALL" ? "" : v);
                  setPage(1);
                }}
                disabled={tenants.isError}
              >
                <SelectTrigger id="audit-tenant-filter" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">全部</SelectItem>
                  {(tenants.data ?? []).map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.tenant_key}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {/* listTenants 挂 user:manage(页面守卫是 audit:view,
                  权限错配时 403)——失败显式禁用+提示,不静默 */}
              {tenants.isError && (
                <p role="alert" className="text-xs text-destructive">
                  租户筛选不可用
                </p>
              )}
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">链路 ID</Label>
              <Input
                value={traceId}
                onChange={(e) => {
                  setTraceId(e.target.value);
                  setPage(1);
                }}
                placeholder="调用链路 ID"
                className="w-52 font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">判定</Label>
              <Select
                value={verdict || "ALL"}
                onValueChange={(v) => {
                  setVerdict(v === "ALL" ? "" : v);
                  setPage(1);
                }}
              >
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">全部</SelectItem>
                  <SelectItem value="PASS">通过</SelectItem>
                  <SelectItem value="BLOCKED">拦截</SelectItem>
                  <SelectItem value="ERROR">失败</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </>
        ) : (
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">动作</Label>
            <Input
              value={action}
              onChange={(e) => {
                setAction(e.target.value);
                setPage(1);
              }}
              placeholder="输入操作代码筛选,如 user.create"
              className="w-52 font-mono text-xs"
            />
          </div>
        )}
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">开始日期</Label>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => {
              setStartDate(e.target.value);
              setPage(1);
            }}
            className="w-36"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">结束日期</Label>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => {
              setEndDate(e.target.value);
              setPage(1);
            }}
            className="w-36"
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

      {/* 查询失败:错误条置于表区上方(UsersPage 同口径),DataView 只管加载/空态 */}
      {section === "sql" && sqlList.isError && <ErrorAlert />}
      {section === "oper" && operList.isError && <ErrorAlert />}

      {section === "sql" ? (
        <DataView
          rows={sqlList.data?.items ?? []}
          rowKey={(it) => it.id}
          isLoading={sqlList.isPending}
          isEmpty={!sqlList.isPending && sqlList.data?.items.length === 0}
          emptyText="暂无审计记录。"
          columns={[
            {
              key: "created_at",
              header: "时间",
              cardPriority: 2,
              cellClassName: "whitespace-nowrap text-muted-foreground",
              render: (it) => formatDateTime(it.created_at, { seconds: true }),
            },
            {
              key: "user",
              header: "用户",
              cardPriority: 3,
              render: (it) => (
                <span
                  className="block max-w-32 truncate"
                  title={it.user_id ? displayUser(it.user_nickname, it.user_username) : "系统"}
                >
                  {it.user_id ? displayUser(it.user_nickname, it.user_username) : "系统"}
                </span>
              ),
            },
            { key: "tenant_key", header: "租户" },
            { key: "datasource_key", header: "数据源" },
            {
              key: "question",
              header: "问句",
              cardPriority: 1,
              render: (it) => (
                <span className="block max-w-56 truncate" title={it.question ?? undefined}>
                  {it.question ?? "—"}
                </span>
              ),
            },
            {
              key: "verdict",
              header: "判定",
              cardPriority: 4,
              render: (it) => <StatusBadge meta={VERDICT_META} value={it.verdict} />,
            },
            {
              key: "duration_ms",
              header: "耗时",
              cardPriority: 5,
              cellClassName: "tabular-nums",
              render: (it) => formatDuration(it.duration_ms),
            },
          ]}
          actions={[{ label: "SQL", onSelect: (it) => setDetail({ kind: "sql", item: it }) }]}
        />
      ) : (
        <DataView
          rows={operList.data?.items ?? []}
          rowKey={(it) => it.id}
          isLoading={operList.isPending}
          isEmpty={!operList.isPending && operList.data?.items.length === 0}
          emptyText="暂无操作记录。"
          columns={[
            {
              key: "created_at",
              header: "时间",
              cardPriority: 2,
              cellClassName: "whitespace-nowrap text-muted-foreground",
              render: (it) => formatDateTime(it.created_at, { seconds: true }),
            },
            {
              key: "user",
              header: "用户",
              cardPriority: 3,
              render: (it) => (
                <span
                  className="block max-w-32 truncate"
                  title={it.user_id ? displayUser(it.user_nickname, it.user_username) : "系统"}
                >
                  {it.user_id ? displayUser(it.user_nickname, it.user_username) : "系统"}
                </span>
              ),
            },
            {
              key: "action",
              header: "动作",
              cardPriority: 1,
              cellClassName: "font-mono text-xs",
              render: (it) => <span className="font-mono text-xs">{it.action}</span>,
            },
            {
              key: "target",
              header: "目标",
              cardPriority: 4,
              render: (it) => (
                <span
                  className="block max-w-48 truncate font-mono text-xs"
                  title={it.target ?? undefined}
                >
                  {it.target ?? "—"}
                </span>
              ),
            },
            {
              key: "ip",
              header: "IP",
              cardPriority: 5,
              cellClassName: "font-mono text-xs",
              render: (it) => it.ip ?? "—",
            },
          ]}
          actions={[{ label: "详情", onSelect: (it) => setDetail({ kind: "oper", item: it }) }]}
        />
      )}

      <Pagination page={page} pageCount={totalPages} total={total ?? 0} onPageChange={setPage} />

      <Dialog open={detail !== null} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent aria-describedby={undefined} className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{detail?.kind === "sql" ? "SQL 详情" : "操作详情"}</DialogTitle>
          </DialogHeader>
          {detail?.kind === "sql" && (
            <div>
              <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
                {detail.item.sql_text}
              </pre>
              {detail.item.block_reason && (
                <p className="mt-1 text-xs text-muted-foreground">
                  拦截原因:{detail.item.block_reason}
                </p>
              )}
              <p
                className="mt-1 max-w-full truncate font-mono text-xs text-muted-foreground"
                title={detail.item.trace_id ?? undefined}
              >
                链路 ID:{detail.item.trace_id ?? "—"}
              </p>
            </div>
          )}
          {detail?.kind === "oper" && (
            <div>
              {/* 管理排查场景需要原始 JSON */}
              <p className="mb-1 text-xs font-medium text-muted-foreground">原始数据(JSON)</p>
              <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
                {JSON.stringify(detail.item.detail ?? {}, null, 2)}
              </pre>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

type SqlItem = NonNullable<Awaited<ReturnType<typeof sqlAuditPage>>["items"][number]>;
type OperItem = NonNullable<Awaited<ReturnType<typeof operLogPage>>["items"][number]>;
