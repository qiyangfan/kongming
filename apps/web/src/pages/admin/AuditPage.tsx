/* 审计页(015,audit spec「SQL 审计查询」「操作日志」):SQL 审计/操作日志
   双区(toggle-group 切换)+筛选+分页+行内展开详情(完整 sql_text/detail)。
   只读页面(归档运维经 API/调度,不在 UI)。权限点 system:audit:view。 */

import { Fragment, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Loader2, ScrollText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { QueryErrorAlert } from "@/components/query-error";
import { operLogPage, sqlAuditPage } from "@/lib/audit-api";
import { systemApi } from "@/lib/system-api";
import { endExclusive } from "@/lib/date-utils";

const PAGE_SIZE = 20;

const VERDICT_META: Record<string, { label: string; variant: "default" | "secondary" | "outline" }> = {
  PASS: { label: "通过", variant: "default" },
  BLOCKED: { label: "拦截", variant: "secondary" },
  ERROR: { label: "失败", variant: "outline" },
};

function fmtTime(v: string | null | undefined): string {
  // 本地时区呈现(R1-B3:UTC 原文直出对账差 8 小时,与 RagPage 对齐)
  return v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—";
}

export default function AuditPage() {
  const [section, setSection] = useState<"sql" | "oper">("sql");
  const [page, setPage] = useState(1);
  const [traceId, setTraceId] = useState("");
  const [verdict, setVerdict] = useState("");
  const [action, setAction] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

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

  // 切区重置分页;末页越界自愈(007/009 范式)
  useEffect(() => {
    setPage(1);
  }, [section]);
  useEffect(() => {
    const t = section === "sql" ? sqlList.data?.total : operList.data?.total;
    if (t !== undefined && t > 0 && page > 1 && (section === "sql" ? sqlList.data?.items.length : operList.data?.items.length) === 0) {
      setPage((p) => p - 1);
    }
  }, [sqlList.data, operList.data, page, section]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">审计</h2>
          <p className="text-sm text-muted-foreground">
            谁问了什么、谁改过什么——SQL 审计与操作日志的可视化台账。
          </p>
        </div>
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
      </div>

      {/* 筛选区 */}
      <div className="flex flex-wrap items-end gap-3">
        {section === "sql" ? (
          <>
            <div className="space-y-1">
              <Label htmlFor="audit-tenant-filter" className="text-xs text-muted-foreground">租户</Label>
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
              {/* R1-F2:listTenants 挂 user:manage(页面守卫是 audit:view,
                  权限错配时 403)——失败显式禁用+提示,不静默 */}
              {tenants.isError && (
                <p role="alert" className="text-xs text-destructive">租户筛选不可用</p>
              )}
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">trace_id</Label>
              <Input
                value={traceId}
                onChange={(e) => {
                  setTraceId(e.target.value);
                  setPage(1);
                }}
                placeholder="链路追踪"
                className="w-52 font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">判定</Label>
              <Select value={verdict || "ALL"} onValueChange={(v) => { setVerdict(v === "ALL" ? "" : v); setPage(1); }}>
                <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
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
              placeholder="如 user.create"
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
      </div>

      {/* 分页表 */}
      <div className="overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            {section === "sql" ? (
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>用户</TableHead>
                <TableHead>租户</TableHead>
                <TableHead>数据源</TableHead>
                <TableHead>问句</TableHead>
                <TableHead>判定</TableHead>
                <TableHead>耗时</TableHead>
                <TableHead className="text-right">详情</TableHead>
              </TableRow>
            ) : (
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>用户</TableHead>
                <TableHead>动作</TableHead>
                <TableHead>目标</TableHead>
                <TableHead>IP</TableHead>
                <TableHead className="text-right">详情</TableHead>
              </TableRow>
            )}
          </TableHeader>
          <TableBody>
            {section === "sql" ? (
              <SqlRows loading={sqlList.isLoading} items={sqlList.data?.items ?? []} error={sqlList.isError} />
            ) : (
              <OperRows loading={operList.isLoading} items={operList.data?.items ?? []} error={operList.isError} />
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>共 {total ?? 0} 条</span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            上一页
          </Button>
          <span>{page} / {totalPages}</span>
          <Button variant="outline" size="xs" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
            下一页
          </Button>
        </div>
      </div>
    </div>
  );
}

type SqlItem = NonNullable<Awaited<ReturnType<typeof sqlAuditPage>>["items"][number]>;
type OperItem = NonNullable<Awaited<ReturnType<typeof operLogPage>>["items"][number]>;

function SqlRows({
  loading,
  items,
  error,
}: {
  loading: boolean;
  items: SqlItem[];
  error?: boolean;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (error) {
    return (
      <TableRow>
        <TableCell colSpan={8} className="py-8">
          <QueryErrorAlert />
        </TableCell>
      </TableRow>
    );
  }
  if (loading) {
    return (
      <TableRow>
        <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
          <Loader2 className="inline size-4 animate-spin" aria-hidden /> 加载中…
        </TableCell>
      </TableRow>
    );
  }
  if (items.length === 0) {
    return (
      <TableRow>
        <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">暂无审计记录。</TableCell>
      </TableRow>
    );
  }
  return (
    <>
      {items.map((it) => (
        <Fragment key={it.id}>
          <TableRow>
            <TableCell className="whitespace-nowrap text-muted-foreground">{fmtTime(it.created_at)}</TableCell>
            <TableCell className="max-w-32 truncate" title={it.user_nickname ?? undefined}>{it.user_nickname ?? it.user_id?.slice(0, 8) ?? "系统"}</TableCell>
            <TableCell>{it.tenant_key}</TableCell>
            <TableCell>{it.datasource_key}</TableCell>
            <TableCell className="max-w-56 truncate" title={it.question ?? undefined}>{it.question ?? "—"}</TableCell>
            <TableCell>
              <Badge variant={VERDICT_META[it.verdict]?.variant ?? "outline"}>
                {VERDICT_META[it.verdict]?.label ?? it.verdict}
              </Badge>
            </TableCell>
            <TableCell className="tabular-nums">{it.duration_ms != null ? `${it.duration_ms}ms` : "—"}</TableCell>
            <TableCell className="text-right">
              <Button variant="ghost" size="xs" onClick={() => setExpanded(expanded === it.id ? null : it.id)}>
                {expanded === it.id ? "收起" : "SQL"}
              </Button>
            </TableCell>
          </TableRow>
          {expanded === it.id && (
            <TableRow>
              <TableCell colSpan={8} className="bg-muted/30">
                <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
                  {it.sql_text}
                </pre>
                {it.block_reason && (
                  <p className="mt-1 text-xs text-muted-foreground">拦截原因:{it.block_reason}</p>
                )}
                <p className="mt-1 font-mono text-xs text-muted-foreground">trace_id: {it.trace_id}</p>
              </TableCell>
            </TableRow>
          )}
        </Fragment>
      ))}
    </>
  );
}

function OperRows({
  loading,
  items,
  error,
}: {
  loading: boolean;
  items: OperItem[];
  error?: boolean;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (error) {
    return (
      <TableRow>
        <TableCell colSpan={6} className="py-8">
          <QueryErrorAlert />
        </TableCell>
      </TableRow>
    );
  }
  if (loading) {
    return (
      <TableRow>
        <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
          <Loader2 className="inline size-4 animate-spin" aria-hidden /> 加载中…
        </TableCell>
      </TableRow>
    );
  }
  if (items.length === 0) {
    return (
      <TableRow>
        <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">暂无操作记录。</TableCell>
      </TableRow>
    );
  }
  return (
    <>
      {items.map((it) => (
        <Fragment key={it.id}>
          <TableRow>
            <TableCell className="whitespace-nowrap text-muted-foreground">{fmtTime(it.created_at)}</TableCell>
            <TableCell className="max-w-32 truncate" title={it.user_nickname ?? undefined}>{it.user_nickname ?? it.user_id?.slice(0, 8) ?? "系统"}</TableCell>
            <TableCell className="font-mono text-xs">{it.action}</TableCell>
            <TableCell className="max-w-48 truncate font-mono text-xs" title={it.target ?? undefined}>{it.target ?? "—"}</TableCell>
            <TableCell className="font-mono text-xs">{it.ip ?? "—"}</TableCell>
            <TableCell className="text-right">
              <Button variant="ghost" size="xs" onClick={() => setExpanded(expanded === it.id ? null : it.id)}>
                {expanded === it.id ? "收起" : "detail"}
              </Button>
            </TableCell>
          </TableRow>
          {expanded === it.id && (
            <TableRow>
              <TableCell colSpan={6} className="bg-muted/30">
                <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
                  {JSON.stringify(it.detail ?? {}, null, 2)}
                </pre>
              </TableCell>
            </TableRow>
          )}
        </Fragment>
      ))}
    </>
  );
}
