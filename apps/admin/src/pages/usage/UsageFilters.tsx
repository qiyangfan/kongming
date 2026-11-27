/* 用量页的筛选栏与明细表列定义。 */

import type { DataViewColumn } from "@kongming/ui/data-view";
import { Input } from "@kongming/ui/input";
import { Label } from "@kongming/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { StatusBadge } from "@kongming/ui/status-badge";
import { formatDateTime, formatDuration } from "@kongming/api-client/datetime";
import type { usagePage } from "@kongming/api-client/usage-api";

import { fmtTokens, LLM_STATUS_META } from "./usage-format";

export function detailColumns(): DataViewColumn<
  Awaited<ReturnType<typeof usagePage>>["rows"][number]
>[] {
  return [
    {
      key: "created_at",
      header: "时间",
      cardPriority: 2,
      cellClassName: "whitespace-nowrap text-muted-foreground",
      render: (r) => formatDateTime(r.created_at),
    },
    { key: "tenant_key", header: "租户", cellClassName: "text-muted-foreground" },
    {
      key: "model",
      header: "模型",
      cardPriority: 1,
      render: (r) => (
        <span className="block max-w-40 truncate font-medium" title={r.model}>
          {r.model}
        </span>
      ),
    },
    { key: "task", header: "任务", cardPriority: 4 },
    {
      key: "protocol",
      header: "协议",
      cellClassName: "text-muted-foreground",
      // null(存量行/旧版上报)展示「-」,不回算不推断
      render: (r) => r.protocol ?? "—",
    },
    {
      key: "status",
      header: "状态",
      cardPriority: 3,
      render: (r) => <StatusBadge meta={LLM_STATUS_META} value={r.status} />,
    },
    {
      key: "total_tokens",
      header: "Tokens",
      cardPriority: 5,
      cellClassName: "text-right",
      render: (r) => fmtTokens(r.total_tokens),
    },
    {
      key: "latency_ms",
      header: "延迟",
      cellClassName: "text-right",
      render: (r) => formatDuration(r.latency_ms),
    },
    {
      key: "trace_id",
      header: "链路",
      render: (r) => (
        <span
          className="block max-w-32 truncate font-mono text-xs text-muted-foreground"
          title={r.trace_id ?? undefined}
        >
          {r.trace_id ?? "—"}
        </span>
      ),
    },
  ];
}

type FiltersProps = {
  model: string;
  onModelChange: (v: string) => void;
  modelOptions: string[];
  task: string;
  onTaskChange: (v: string) => void;
  taskOptions: string[];
  status: string;
  onStatusChange: (v: string) => void;
  tenantId: string;
  onTenantChange: (v: string) => void;
  tenantsSource: { data?: { id: string; tenant_key: string }[] | undefined; isError: boolean };
  protocol: string;
  onProtocolChange: (v: string) => void;
  traceId: string;
  onTraceIdChange: (v: string) => void;
};

export function UsageFilters(p: FiltersProps) {
  return (
    <div className="flex flex-wrap items-end gap-3 border-b px-4 py-3">
      <div className="space-y-1">
        <Label className="text-xs">模型</Label>
        <Select value={p.model || "ALL"} onValueChange={(v) => p.onModelChange(all(v))}>
          <SelectTrigger size="sm" className="w-40" aria-label="模型筛选">
            <SelectValue placeholder="全部模型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部模型</SelectItem>
            {p.modelOptions.map((m) => (
              <SelectItem key={m} value={m}>
                {m}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">任务</Label>
        <Select value={p.task || "ALL"} onValueChange={(v) => p.onTaskChange(all(v))}>
          <SelectTrigger size="sm" className="w-36" aria-label="任务筛选">
            <SelectValue placeholder="全部任务" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部任务</SelectItem>
            {p.taskOptions.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">状态</Label>
        <Select value={p.status || "ALL"} onValueChange={(v) => p.onStatusChange(all(v))}>
          <SelectTrigger size="sm" className="w-24">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部</SelectItem>
            <SelectItem value="SUCCESS">成功</SelectItem>
            <SelectItem value="ERROR">失败</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <TenantFilter p={p} />
      <div className="space-y-1">
        <Label className="text-xs">协议</Label>
        <Select value={p.protocol || "ALL"} onValueChange={(v) => p.onProtocolChange(all(v))}>
          <SelectTrigger size="sm" className="w-32" aria-label="协议筛选">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部</SelectItem>
            <SelectItem value="openai">openai</SelectItem>
            <SelectItem value="anthropic">anthropic</SelectItem>
            <SelectItem value="responses">responses</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">链路 ID</Label>
        <Input
          size="sm"
          className="w-44"
          placeholder="精确匹配"
          maxLength={64}
          value={p.traceId}
          onChange={(e) => p.onTraceIdChange(e.target.value)}
        />
      </div>
    </div>
  );
}

/** 租户筛选:租户清单权限错配(403)时禁用下拉并显式提示。 */
function TenantFilter({ p }: { p: FiltersProps }) {
  return (
    <div className="space-y-1">
      <Label htmlFor="usage-tenant-filter" className="text-xs">
        租户
      </Label>
      <Select
        value={p.tenantId || "ALL"}
        onValueChange={(v) => p.onTenantChange(all(v))}
        disabled={p.tenantsSource.isError}
      >
        <SelectTrigger id="usage-tenant-filter" size="sm" className="w-32">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="ALL">全部</SelectItem>
          {(p.tenantsSource.data ?? []).map((t) => (
            <SelectItem key={t.id} value={t.id}>
              {t.tenant_key}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {/* listTenants 挂 user:manage(页面守卫是 llm-usage:view,
          权限错配时 403)——失败显式禁用+提示,不静默 */}
      {p.tenantsSource.isError && (
        <p role="alert" className="text-xs text-destructive">
          租户筛选不可用
        </p>
      )}
    </div>
  );
}

/** 「全部」哨兵值换回空串,表示该维度不过滤。 */
function all(v: string): string {
  return v === "ALL" ? "" : v;
}
