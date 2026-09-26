/* 护栏页(020,prompt-guardrails spec「规则可配置」「护栏审计与查询」):
   规则清单(启停/动作/阈值即时生效)+ 触发审计分页(类型/规则/动作/时间)。
   权限点 system:guardrail:manage。 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";

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
import { Switch } from "@/components/ui/switch";
import { ErrorAlert } from "@/components/error-alert";
import { DateRangeQuick } from "@/components/date-range-quick";
import { guardrailAuditPage, guardrailRules, updateGuardrailRule } from "@/lib/guardrail-api";
import { endExclusive } from "@/lib/date-utils";
import { formatDateTime } from "@/lib/datetime";

const PAGE_SIZE = 20;

const TYPE_LABELS: Record<string, string> = {
  input: "输入",
  output: "输出",
  tool_intent: "工具意图",
};

const ACTION_META: StatusMeta = {
  blocked: { label: "拦截", variant: "secondary" },
  masked: { label: "脱敏", variant: "default" },
};

export default function GuardrailsPage() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [ftype, setFtype] = useState("");
  const [faction, setFaction] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [toggleError, setToggleError] = useState<string | null>(null);

  const rules = useQuery({ queryKey: ["guardrails", "rules"], queryFn: guardrailRules });
  const audit = useQuery({
    queryKey: ["guardrails", "audit", page, ftype, faction, startDate, endDate],
    queryFn: () =>
      guardrailAuditPage({
        page,
        size: PAGE_SIZE,
        ...(ftype ? { guardrail_type: ftype as "input" | "output" | "tool_intent" } : {}),
        ...(faction ? { action: faction as "blocked" | "masked" } : {}),
        ...(startDate ? { start: `${startDate}T00:00:00Z` } : {}),
        ...(endDate ? { end: endExclusive(endDate) } : {}),
      }),
  });

  const toggle = useMutation({
    // R1-M5:启停只传 enabled——不带 action/threshold(服务端 None=不触动,
    // 避免把合并视图值固化成陈旧锚点/清掉既有动作覆盖)
    mutationFn: (input: { name: string; enabled: boolean }) =>
      updateGuardrailRule(input.name, { enabled: input.enabled }),
    onSuccess: () => {
      setToggleError(null);
      void qc.invalidateQueries({ queryKey: ["guardrails", "rules"] });
    },
    onError: (err) => {
      setToggleError(err instanceof Error ? err.message : "保存失败");
    },
  });

  const editMeta = useMutation({
    mutationFn: (input: {
      name: string;
      enabled: boolean;
      action?: "blocked" | "masked" | null;
      threshold?: number | null;
    }) =>
      updateGuardrailRule(input.name, {
        enabled: input.enabled,
        action: input.action ?? null,
        threshold: input.threshold ?? null,
      }),
    onSuccess: () => {
      setToggleError(null);
      void qc.invalidateQueries({ queryKey: ["guardrails", "rules"] });
    },
    onError: (err) => {
      setToggleError(err instanceof Error ? err.message : "保存失败");
    },
  });

  const totalPages = Math.max(1, Math.ceil((audit.data?.total ?? 0) / PAGE_SIZE));
  useEffect(() => {
    const t = audit.data?.total;
    if (t !== undefined && t > 0 && page > 1 && audit.data?.items.length === 0) {
      setPage((p) => p - 1);
    }
  }, [audit.data, page]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="护栏"
        description="AI 问答的安全防线:拦截诱导攻击、保护敏感信息、管控高危操作。可调整规则并查看触发记录。"
      />

      {rules.isError && <ErrorAlert />}
      <DataView
        rows={rules.data?.rules ?? []}
        rowKey={(r) => r.name}
        isLoading={rules.isPending}
        isEmpty={!rules.isPending && rules.data?.rules.length === 0}
        emptyText="暂无护栏规则"
        columns={[
          {
            key: "name",
            header: "规则",
            cardPriority: 1,
            cellClassName: "font-mono text-xs font-medium",
            render: (r) => (
              <span className="flex items-center gap-1.5">
                <ShieldCheck className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                <span className="truncate" title={r.name}>
                  {r.name}
                </span>
                {r.overridden && (
                  <Badge variant="outline" className="shrink-0">
                    已调整
                  </Badge>
                )}
              </span>
            ),
          },
          {
            key: "guardrail_type",
            header: "层",
            cardPriority: 2,
            render: (r) => (
              <Badge variant="secondary">{TYPE_LABELS[r.guardrail_type] ?? r.guardrail_type}</Badge>
            ),
          },
          {
            key: "action",
            header: "动作",
            cardPriority: 3,
            render: (r) =>
              r.maskable ? (
                <Select
                  value={r.action}
                  onValueChange={(v) =>
                    editMeta.mutate({
                      name: r.name,
                      enabled: r.enabled,
                      action: v as "blocked" | "masked",
                    })
                  }
                  disabled={editMeta.isPending}
                >
                  <SelectTrigger size="sm" className="w-24" aria-label={`${r.name} 动作`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="blocked">拦截</SelectItem>
                    <SelectItem value="masked">脱敏</SelectItem>
                  </SelectContent>
                </Select>
              ) : (
                // action 契约窄联合;未知值回落 default,与原 ?? "default" 等价
                <StatusBadge meta={ACTION_META} value={r.action} fallbackVariant="default" />
              ),
          },
          {
            key: "description",
            header: "描述",
            cellClassName: "text-sm text-muted-foreground",
            render: (r) => (
              <span className="block max-w-md truncate" title={r.description}>
                {r.description}
              </span>
            ),
          },
          {
            key: "threshold",
            header: "阈值",
            cellClassName: "text-xs",
            render: (r) =>
              r.guardrail_type === "tool_intent" ? (
                <Input
                  type="number"
                  min={1}
                  max={100}
                  defaultValue={r.threshold ?? 1}
                  size="sm"
                  className="w-20"
                  aria-label={`${r.name} 阈值`}
                  disabled={editMeta.isPending}
                  onBlur={(e) => {
                    const v = Number.parseInt(e.target.value, 10);
                    if (Number.isFinite(v) && v >= 1 && v <= 100 && v !== r.threshold) {
                      editMeta.mutate({
                        name: r.name,
                        enabled: r.enabled,
                        threshold: v,
                      });
                    }
                  }}
                />
              ) : (
                "—"
              ),
          },
          {
            key: "enabled",
            header: "启用",
            cardPriority: 4,
            render: (r) => (
              <Switch
                checked={r.enabled}
                disabled={toggle.isPending}
                onCheckedChange={(on) => toggle.mutate({ name: r.name, enabled: on })}
                aria-label={`启用 ${r.name}`}
              />
            ),
          },
        ]}
      />
      {toggleError && (
        <p className="text-xs text-destructive" role="alert">
          {toggleError}
        </p>
      )}

      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">触发审计</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="gr-type" className="text-xs">
              层
            </Label>
            <Select
              value={ftype || "__all__"}
              onValueChange={(v) => {
                setFtype(v === "__all__" ? "" : v);
                setPage(1);
              }}
            >
              <SelectTrigger id="gr-type" className="w-36" aria-label="按层筛选">
                <SelectValue placeholder="全部" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部</SelectItem>
                {Object.entries(TYPE_LABELS).map(([k, l]) => (
                  <SelectItem key={k} value={k}>
                    {l}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="gr-action" className="text-xs">
              动作
            </Label>
            <Select
              value={faction || "__all__"}
              onValueChange={(v) => {
                setFaction(v === "__all__" ? "" : v);
                setPage(1);
              }}
            >
              <SelectTrigger id="gr-action" className="w-36" aria-label="按动作筛选">
                <SelectValue placeholder="全部" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部</SelectItem>
                <SelectItem value="blocked">拦截</SelectItem>
                <SelectItem value="masked">脱敏</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="gr-start" className="text-xs">
              开始日期
            </Label>
            <Input
              id="gr-start"
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
            <Label htmlFor="gr-end" className="text-xs">
              结束日期
            </Label>
            <Input
              id="gr-end"
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
          emptyText="暂无护栏触发记录"
          columns={[
            {
              key: "created_at",
              header: "时间",
              cardPriority: 2,
              cellClassName: "text-xs text-muted-foreground",
              render: (it) => formatDateTime(it.created_at, { seconds: true }),
            },
            {
              key: "guardrail_type",
              header: "层",
              cardPriority: 3,
              render: (it) => (
                <Badge variant="secondary">
                  {TYPE_LABELS[it.guardrail_type] ?? it.guardrail_type}
                </Badge>
              ),
            },
            {
              key: "rule_name",
              header: "规则",
              cardPriority: 1,
              cellClassName: "font-mono text-xs",
              render: (it) => <span className="font-mono text-xs">{it.rule_name}</span>,
            },
            {
              key: "action",
              header: "动作",
              cardPriority: 4,
              render: (it) => (
                <StatusBadge meta={ACTION_META} value={it.action} fallbackVariant="default" />
              ),
            },
            {
              key: "content_digest",
              header: "内容摘要(脱敏)",
              cellClassName: "text-xs text-muted-foreground",
              render: (it) => (
                <span className="block max-w-96 truncate" title={it.content_digest || undefined}>
                  {it.content_digest || "—"}
                </span>
              ),
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
