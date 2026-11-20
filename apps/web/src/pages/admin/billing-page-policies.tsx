/* 计费页配额策略区块:租户/套餐选择、策略清单与新增表单。 */

import type { SubmitEvent } from "react";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DataView } from "@/components/data-view";
import { ErrorAlert } from "@/components/error-alert";
import { FormRow } from "@/components/form-row";
import type {
  BotBrief,
  PlanItem,
  PolicyItem,
  TeamBrief,
  TenantBrief,
  UserBrief,
} from "@/lib/billing-api";
import { DIM_TEXT, SCOPE_TEXT } from "@/lib/billing-labels";
import { li2yuan } from "@/lib/money";
import { formatNumber } from "@/lib/datetime";
import type { PolicyForm } from "./billing-page-model";
import type { QueryLike } from "./billing-page-sections";

const preventDefault = (e: SubmitEvent<HTMLFormElement>) => e.preventDefault();

// 策略范围列:USER/TEAM 级以清单数据源回显名称;候选未含(已删除)
// 不露 UUID,hover 可得原 id
function ScopeCell({
  policy,
  bots,
  users,
  teams,
}: {
  policy: PolicyItem;
  bots: BotBrief[] | undefined;
  users: UserBrief[] | undefined;
  teams: TeamBrief[] | undefined;
}) {
  const unknown = (label: string) => (
    <Badge variant="outline" title={policy.scope_ref ?? undefined}>
      {label}
    </Badge>
  );
  if (policy.scope === "BOT") {
    const botKey = (bots ?? []).find((b) => b.id === policy.scope_ref)?.bot_key;
    return botKey ? <Badge variant="outline">助手:{botKey}</Badge> : unknown("未知助手");
  }
  if (policy.scope === "USER") {
    const username = (users ?? []).find((u) => u.id === policy.scope_ref)?.username;
    return username ? <Badge variant="outline">用户:{username}</Badge> : unknown("未知用户");
  }
  if (policy.scope === "TEAM") {
    const teamKey = (teams ?? []).find((t) => t.id === policy.scope_ref)?.team_key;
    return teamKey ? <Badge variant="outline">团队:{teamKey}</Badge> : unknown("未知团队");
  }
  return <Badge variant="secondary">租户</Badge>;
}

// 策略范围选择器候选:按 scope 取对应清单(bots/users/teams)
function scopeCandidates(opts: {
  scope: string;
  bots: BotBrief[] | undefined;
  users: UserBrief[] | undefined;
  teams: TeamBrief[] | undefined;
}): { id: string; label: string }[] {
  if (opts.scope === "BOT") return (opts.bots ?? []).map((b) => ({ id: b.id, label: b.bot_key }));
  if (opts.scope === "USER")
    return (opts.users ?? []).map((u) => ({ id: u.id, label: u.username }));
  return (opts.teams ?? []).map((t) => ({ id: t.id, label: t.team_key }));
}

type PolicyData = {
  tenants: QueryLike<TenantBrief[]>;
  plans: QueryLike<PlanItem[]>;
  policies: QueryLike<PolicyItem[]>;
  bots: QueryLike<BotBrief[]>;
  users: QueryLike<UserBrief[]>;
  teams: QueryLike<TeamBrief[]>;
  tenantPlan: QueryLike<PlanItem | null>;
};

function PolicyFormFields({
  form,
  onChange,
  source,
}: {
  form: PolicyForm;
  onChange: (f: PolicyForm) => void;
  source: Pick<PolicyData, "bots" | "users" | "teams">;
}) {
  return (
    <>
      <FormRow label="范围">
        <Select
          value={form.scope}
          onValueChange={(v) => onChange({ ...form, scope: v, scope_ref: "" })}
        >
          <SelectTrigger className="w-28" aria-label="范围">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="TENANT">{SCOPE_TEXT.TENANT}</SelectItem>
            <SelectItem value="BOT">{SCOPE_TEXT.BOT}</SelectItem>
            <SelectItem value="USER">{SCOPE_TEXT.USER}</SelectItem>
            <SelectItem value="TEAM">{SCOPE_TEXT.TEAM}</SelectItem>
          </SelectContent>
        </Select>
      </FormRow>
      {form.scope !== "TENANT" && (
        <FormRow label={SCOPE_TEXT[form.scope] ?? form.scope}>
          <Select value={form.scope_ref} onValueChange={(v) => onChange({ ...form, scope_ref: v })}>
            <SelectTrigger className="w-40" aria-label={`选择${SCOPE_TEXT[form.scope] ?? ""}`}>
              <SelectValue placeholder={`选择${SCOPE_TEXT[form.scope] ?? ""}`} />
            </SelectTrigger>
            <SelectContent>
              {scopeCandidates({
                scope: form.scope,
                bots: source.bots.data,
                users: source.users.data,
                teams: source.teams.data,
              }).map((o) => (
                <SelectItem key={o.id} value={o.id}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormRow>
      )}
      <FormRow label="维度">
        <Select
          value={form.dimension}
          onValueChange={(v) =>
            // 维度切换=单位语义失效(token/次/元),上限清零重录,防跨单位误提交
            onChange({ ...form, dimension: v, limit_value: "0" })
          }
        >
          <SelectTrigger className="w-32" aria-label="维度">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="TOKENS">{DIM_TEXT.TOKENS}</SelectItem>
            <SelectItem value="CALLS">{DIM_TEXT.CALLS}</SelectItem>
            <SelectItem value="COST">{DIM_TEXT.COST}</SelectItem>
          </SelectContent>
        </Select>
      </FormRow>
      <FormRow label={`上限${form.dimension === "COST" ? "(元)" : ""}`}>
        <Input
          value={form.limit_value}
          onChange={(e) => onChange({ ...form, limit_value: e.target.value })}
          placeholder={form.dimension === "COST" ? "如 10.00" : "如 100000"}
          aria-label="上限"
        />
      </FormRow>
      <FormRow label="预警%">
        <Input
          value={form.warn_pct}
          onChange={(e) => onChange({ ...form, warn_pct: e.target.value })}
          className="w-20"
          aria-label="预警百分比"
        />
      </FormRow>
    </>
  );
}

// CALLS 维度仅租户级可用(调用次数不按资源/用户/团队立约)
function saveDisabled(form: PolicyForm, pending: boolean): boolean {
  return (
    pending || (form.scope !== "TENANT" && (form.scope_ref === "" || form.dimension === "CALLS"))
  );
}

function PolicyNotes({
  form,
  tenantPlan,
}: {
  form: PolicyForm;
  tenantPlan: PlanItem | null | undefined;
}) {
  return (
    <>
      {form.scope !== "TENANT" && form.dimension === "CALLS" && (
        <p className="text-xs text-muted-foreground" role="note">
          提示:「{DIM_TEXT.CALLS}」维度仅支持「{SCOPE_TEXT.TENANT}
          」范围(调用次数不按资源/用户/团队立约)。
        </p>
      )}
      {form.dimension === "COST" && tenantPlan == null && (
        <p className="text-xs text-muted-foreground" role="note">
          提示:该租户未绑定套餐,成本单价按 0 计,「{DIM_TEXT.COST}」上限不会触发限量。
        </p>
      )}
    </>
  );
}

export function PoliciesSection({
  tenantId,
  onTenantIdChange,
  data,
  form,
  onFormChange,
  policyError,
  savingPending,
  bindingPending,
  onSave,
  onBind,
  onDelete,
}: {
  tenantId: string;
  onTenantIdChange: (v: string) => void;
  data: PolicyData;
  form: PolicyForm;
  onFormChange: (f: PolicyForm) => void;
  policyError: string;
  savingPending: boolean;
  bindingPending: boolean;
  onSave: () => void;
  onBind: (v: string | null) => void;
  onDelete: (id: string) => void;
}) {
  const { tenants, plans, policies, bots, users, teams, tenantPlan } = data;
  return (
    <section className="space-y-3 rounded-lg border border-border p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">租户配额策略</h2>
        <div className="flex items-center gap-2">
          <Select value={tenantId} onValueChange={onTenantIdChange}>
            <SelectTrigger className="w-44" aria-label="选择租户">
              <SelectValue placeholder="选择租户" />
            </SelectTrigger>
            <SelectContent>
              {(tenants.data ?? []).map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.tenant_key}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {tenantId && (
            <Select
              disabled={bindingPending}
              value={tenantPlan.data?.id ?? undefined}
              onValueChange={(v) => onBind(v === "_none" ? null : v)}
            >
              <SelectTrigger className="w-40" aria-label="选择套餐">
                <SelectValue placeholder="选择套餐…" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_none">解绑</SelectItem>
                {(plans.data ?? []).map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      </div>
      {tenantId && (
        <>
          {policies.isError && <ErrorAlert />}
          <DataView
            rows={policies.data ?? []}
            rowKey={(p) => p.id}
            isLoading={policies.isPending}
            isEmpty={!policies.isPending && (policies.data?.length ?? 0) === 0}
            emptyText="无策略(不限量)"
            columns={[
              {
                key: "scope",
                header: "范围",
                cardPriority: 1,
                cellClassName: "text-xs",
                render: (p) => (
                  <ScopeCell policy={p} bots={bots.data} users={users.data} teams={teams.data} />
                ),
              },
              {
                key: "dimension",
                header: "维度",
                cardPriority: 2,
                render: (p) => (
                  <Badge variant="secondary">
                    {DIM_TEXT[p.dimension as keyof typeof DIM_TEXT] ?? p.dimension}
                  </Badge>
                ),
              },
              {
                key: "limit_value",
                header: "上限(0=不限)",
                cardPriority: 3,
                headClassName: "text-right",
                cellClassName: "text-right tabular-nums",
                render: (p) =>
                  p.dimension === "COST"
                    ? `¥${li2yuan(p.limit_value)}`
                    : formatNumber(p.limit_value),
              },
              {
                key: "warn_pct",
                header: "预警%",
                cardPriority: 4,
                headClassName: "text-right",
                cellClassName: "text-right tabular-nums",
                render: (p) => `${p.warn_pct}%`,
              },
            ]}
            actions={[{ label: "删除", destructive: true, onSelect: (p) => onDelete(p.id) }]}
          />
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              preventDefault(e);
              onSave();
            }}
          >
            <PolicyFormFields form={form} onChange={onFormChange} source={{ bots, users, teams }} />
            <Button type="submit" disabled={saveDisabled(form, savingPending)}>
              {savingPending && <Loader2 className="animate-spin" aria-hidden />}
              保存
            </Button>
          </form>
          <PolicyNotes form={form} tenantPlan={tenantPlan.data} />
          {policyError && (
            <p role="alert" className="text-sm text-destructive">
              {policyError}
            </p>
          )}
        </>
      )}
    </section>
  );
}
