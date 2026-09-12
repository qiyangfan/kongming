/* 护栏页(020,prompt-guardrails spec「规则可配置」「护栏审计与查询」):
   规则清单(启停/动作/阈值即时生效)+ 触发审计分页(类型/规则/动作/时间)。
   权限点 system:guardrail:manage。 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { QueryErrorAlert } from "@/components/query-error";
import { guardrailAuditPage, guardrailRules, updateGuardrailRule } from "@/lib/guardrail-api";

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

function fmtTime(v: string | null | undefined): string {
  return v ? new Date(v).toLocaleString("zh-CN", { hour12: false }) : "—";
}

/** 结束日期 → 排他上界次日零点(019 R1-D1 同款) */
function endExclusive(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString();
}

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

      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>规则</TableHead>
              <TableHead>层</TableHead>
              <TableHead>动作</TableHead>
              <TableHead>描述</TableHead>
              <TableHead>阈值</TableHead>
              <TableHead>启用</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rules.isError ? (
              <TableRow>
                <TableCell colSpan={6}>
                  <QueryErrorAlert />
                </TableCell>
              </TableRow>
            ) : rules.isLoading ? (
              <TableRow>
                <TableCell colSpan={6} className="py-6 text-center">
                  <Loader2
                    className="mx-auto size-4 animate-spin text-muted-foreground"
                    aria-hidden
                  />
                </TableCell>
              </TableRow>
            ) : (
              rules.data?.rules.map((r) => (
                <TableRow key={r.name}>
                  <TableCell className="font-mono text-xs font-medium">
                    <span className="flex items-center gap-1.5">
                      <ShieldCheck
                        className="size-3.5 shrink-0 text-muted-foreground"
                        aria-hidden
                      />
                      <span className="truncate" title={r.name}>
                        {r.name}
                      </span>
                      {r.overridden && (
                        <Badge variant="outline" className="shrink-0">
                          已调整
                        </Badge>
                      )}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">
                      {TYPE_LABELS[r.guardrail_type] ?? r.guardrail_type}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {r.maskable ? (
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
                    )}
                  </TableCell>
                  <TableCell
                    className="max-w-md truncate text-sm text-muted-foreground"
                    title={r.description}
                  >
                    {r.description}
                  </TableCell>
                  <TableCell className="text-xs">
                    {r.guardrail_type === "tool_intent" ? (
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
                    )}
                  </TableCell>
                  <TableCell>
                    <Switch
                      checked={r.enabled}
                      disabled={toggle.isPending}
                      onCheckedChange={(on) => toggle.mutate({ name: r.name, enabled: on })}
                      aria-label={`启用 ${r.name}`}
                    />
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
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
        </div>

        <div className="rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>层</TableHead>
                <TableHead>规则</TableHead>
                <TableHead>动作</TableHead>
                <TableHead>内容摘要(脱敏)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {audit.isError ? (
                <TableRow>
                  <TableCell colSpan={5}>
                    <QueryErrorAlert />
                  </TableCell>
                </TableRow>
              ) : audit.isLoading ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-6 text-center">
                    <Loader2
                      className="mx-auto size-4 animate-spin text-muted-foreground"
                      aria-hidden
                    />
                  </TableCell>
                </TableRow>
              ) : audit.data?.items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-6 text-center text-sm text-muted-foreground">
                    暂无护栏触发记录
                  </TableCell>
                </TableRow>
              ) : (
                audit.data?.items.map((it) => (
                  <TableRow key={it.id}>
                    <TableCell className="text-xs text-muted-foreground">
                      {fmtTime(it.created_at)}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">
                        {TYPE_LABELS[it.guardrail_type] ?? it.guardrail_type}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{it.rule_name}</TableCell>
                    <TableCell>
                      <StatusBadge meta={ACTION_META} value={it.action} fallbackVariant="default" />
                    </TableCell>
                    <TableCell
                      className="max-w-96 truncate text-xs text-muted-foreground"
                      title={it.content_digest || undefined}
                    >
                      {it.content_digest || "—"}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

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
