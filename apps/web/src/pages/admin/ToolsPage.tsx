/* 工具页(019,tool-calling spec「工具管理与审计查询」):已注册工具清单
   (只读)+ 工具执行审计分页(筛选:工具名/状态/时间范围;时间本地化)。
   权限点 system:tool:manage。 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Wrench } from "lucide-react";

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
import { QueryErrorAlert } from "@/components/query-error";
import { toolAuditPage, toolList } from "@/lib/toolcall-api";
import { endExclusive } from "@/lib/date-utils";

const PAGE_SIZE = 20;

const STATUS_META: Record<string, { label: string; variant: "default" | "secondary" | "outline" | "destructive" }> = {
  EXECUTED: { label: "已执行", variant: "default" },
  REJECTED: { label: "已拒绝", variant: "secondary" },
  ERROR: { label: "失败", variant: "destructive" },
  RATE_LIMITED: { label: "已限流", variant: "outline" },
  PENDING_CONFIRM: { label: "待确认", variant: "outline" },
  CONFIRM_REJECTED: { label: "已拒绝确认", variant: "secondary" },
  CONFIRM_EXPIRED: { label: "确认过期", variant: "secondary" },
};

function fmtTime(v: string | null | undefined): string {
  return v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—";
}

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
      <div>
        <h2 className="text-lg font-semibold">工具</h2>
        <p className="text-sm text-muted-foreground">
          AI 可使用的内置工具清单,以及每次工具执行的审计记录。
        </p>
      </div>

      {/* 注册表清单(只读) */}
      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>工具</TableHead>
              <TableHead>描述</TableHead>
              <TableHead>权限点</TableHead>
              <TableHead>属性</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tools.isError ? (
              <TableRow>
                <TableCell colSpan={4}>
                  <QueryErrorAlert />
                </TableCell>
              </TableRow>
            ) : tools.isLoading ? (
              <TableRow>
                <TableCell colSpan={4} className="py-6 text-center">
                  <Loader2 className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
                </TableCell>
              </TableRow>
            ) : (
              tools.data?.tools.map((t) => (
                <TableRow key={t.name}>
                  <TableCell className="font-mono text-xs font-medium">
                    <span className="flex items-center gap-1.5">
                      <Wrench className="size-3.5 text-muted-foreground" aria-hidden />
                      {t.name}
                    </span>
                  </TableCell>
                  <TableCell className="max-w-md truncate text-sm text-muted-foreground" title={t.description}>
                    {t.description}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{t.perm_code}</TableCell>
                  <TableCell>
                    <span className="flex gap-1">
                      <Badge variant={t.write ? "destructive" : "secondary"}>
                        {t.write ? "写" : "只读"}
                      </Badge>
                      {t.require_confirm && <Badge variant="outline">需确认</Badge>}
                    </span>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* 执行审计 */}
      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">执行审计</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="flt-tool" className="text-xs">工具</Label>
            <Select
              value={toolName || "__all__"}
              onValueChange={(v) => { setToolName(v === "__all__" ? "" : v); setPage(1); }}
            >
              <SelectTrigger id="flt-tool" className="w-52" aria-label="按工具筛选">
                <SelectValue placeholder="全部工具" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部工具</SelectItem>
                {tools.data?.tools.map((t) => (
                  <SelectItem key={t.name} value={t.name}>{t.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="flt-status" className="text-xs">状态</Label>
            <Select
              value={status || "__all__"}
              onValueChange={(v) => { setStatus(v === "__all__" ? "" : v); setPage(1); }}
            >
              <SelectTrigger id="flt-status" className="w-40" aria-label="按状态筛选">
                <SelectValue placeholder="全部状态" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部状态</SelectItem>
                {Object.entries(STATUS_META).map(([k, m]) => (
                  <SelectItem key={k} value={k}>{m.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="flt-start" className="text-xs">开始日期</Label>
            <Input id="flt-start" type="date" value={startDate}
              onChange={(e) => { setStartDate(e.target.value); setPage(1); }} className="w-40" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="flt-end" className="text-xs">结束日期</Label>
            <Input id="flt-end" type="date" value={endDate}
              onChange={(e) => { setEndDate(e.target.value); setPage(1); }} className="w-40" />
          </div>
        </div>

        <div className="rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>工具</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>参数摘要</TableHead>
                <TableHead>耗时</TableHead>
                <TableHead>数据源</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {audit.isError ? (
                <TableRow>
                  <TableCell colSpan={6}>
                    <QueryErrorAlert />
                  </TableCell>
                </TableRow>
              ) : audit.isLoading ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-6 text-center">
                    <Loader2 className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
                  </TableCell>
                </TableRow>
              ) : audit.data?.items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-6 text-center text-sm text-muted-foreground">
                    暂无工具执行记录
                  </TableCell>
                </TableRow>
              ) : (
                audit.data?.items.map((it) => (
                  <TableRow key={it.id}>
                    <TableCell className="text-xs text-muted-foreground">
                      {fmtTime(it.created_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{it.tool_name}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_META[it.status]?.variant ?? "outline"}>
                        {STATUS_META[it.status]?.label ?? it.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="max-w-72 truncate text-xs text-muted-foreground" title={it.args_digest || undefined}>
                      {it.args_digest || "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {it.duration_ms !== null && it.duration_ms !== undefined
                        ? `${it.duration_ms}ms`
                        : "—"}
                    </TableCell>
                    <TableCell className="text-xs">{it.datasource_key ?? "—"}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>共 {audit.data?.total ?? 0} 条</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              上一页
            </Button>
            <span>{page} / {totalPages}</span>
            <Button size="sm" variant="outline" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
              下一页
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
