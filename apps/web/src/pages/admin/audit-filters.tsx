/* 审计页筛选区:SQL 审计的租户/链路 ID/判定,操作日志的动作,以及日期区间。 */

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DateRangeQuick } from "@/components/date-range-quick";

export interface AuditFilters {
  traceId: string;
  verdict: string;
  action: string;
  tenantId: string;
  startDate: string;
  endDate: string;
}

interface AuditFiltersBarProps {
  section: "sql" | "oper";
  filters: AuditFilters;
  /** 任一条件变化:调用方据此重置分页 */
  onChange: (patch: Partial<AuditFilters>) => void;
  tenants: { id: string; tenant_key: string }[] | undefined;
  tenantsError: boolean;
}

/** 租户下拉;listTenants 挂 user:manage(页面守卫是 audit:view,
 * 权限错配时 403)——失败显式禁用+提示,不静默。 */
function TenantFilter(props: AuditFiltersBarProps) {
  return (
    <div className="space-y-1">
      <Label htmlFor="audit-tenant-filter" className="text-xs text-muted-foreground">
        租户
      </Label>
      <Select
        value={props.filters.tenantId || "ALL"}
        onValueChange={(v) => props.onChange({ tenantId: v === "ALL" ? "" : v })}
        disabled={props.tenantsError}
      >
        <SelectTrigger id="audit-tenant-filter" className="w-32">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="ALL">全部</SelectItem>
          {(props.tenants ?? []).map((t) => (
            <SelectItem key={t.id} value={t.id}>
              {t.tenant_key}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {props.tenantsError && (
        <p role="alert" className="text-xs text-destructive">
          租户筛选不可用
        </p>
      )}
    </div>
  );
}

/** SQL 审计的链路 ID 与判定两栏。 */
function TraceVerdictFilters(props: AuditFiltersBarProps) {
  return (
    <>
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">链路 ID</Label>
        <Input
          value={props.filters.traceId}
          onChange={(e) => props.onChange({ traceId: e.target.value })}
          placeholder="调用链路 ID"
          className="w-52 font-mono text-xs"
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">判定</Label>
        <Select
          value={props.filters.verdict || "ALL"}
          onValueChange={(v) => props.onChange({ verdict: v === "ALL" ? "" : v })}
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
  );
}

/** 操作日志的动作筛选。 */
function ActionFilter(props: AuditFiltersBarProps) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">动作</Label>
      <Input
        value={props.filters.action}
        onChange={(e) => props.onChange({ action: e.target.value })}
        placeholder="输入操作代码筛选,如 user.create"
        className="w-52 font-mono text-xs"
      />
    </div>
  );
}

/** 审计筛选条(分区不同,SQL 审计与操作日志各显示自己的条件)。 */
export function AuditFiltersBar(props: AuditFiltersBarProps) {
  const { filters, onChange } = props;
  return (
    <div className="flex flex-wrap items-end gap-3">
      {props.section === "sql" ? (
        <>
          <TenantFilter {...props} />
          <TraceVerdictFilters {...props} />
        </>
      ) : (
        <ActionFilter {...props} />
      )}
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">开始日期</Label>
        <Input
          type="date"
          value={filters.startDate}
          onChange={(e) => onChange({ startDate: e.target.value })}
          className="w-36"
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">结束日期</Label>
        <Input
          type="date"
          value={filters.endDate}
          onChange={(e) => onChange({ endDate: e.target.value })}
          className="w-36"
        />
      </div>
      <DateRangeQuick onPick={(s, e) => onChange({ startDate: s, endDate: e })} />
    </div>
  );
}
