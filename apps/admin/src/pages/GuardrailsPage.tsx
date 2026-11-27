/* 管理护栏规则和触发审计；需要 system:guardrail:manage 权限。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "@kongming/ui/page-header";
import { ErrorAlert } from "@kongming/ui/error-alert";
import {
  guardrailAuditPage,
  guardrailRules,
  updateGuardrailRule,
} from "@kongming/api-client/guardrail-api";
import { endExclusive } from "@kongming/api-client/date-utils";

import { usePageFallback } from "./admin-filter-bits";
import { GuardrailAuditSection } from "./guardrails-audit-section";
import { GuardrailRulesTable } from "./guardrails-rules-table";

const PAGE_SIZE = 20;

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

  const onSaved = () => {
    setToggleError(null);
    void qc.invalidateQueries({ queryKey: ["guardrails", "rules"] });
  };
  const onSaveError = (err: unknown) =>
    setToggleError(err instanceof Error ? err.message : "保存失败");

  const toggle = useMutation({
    // 启停只传 enabled——不带 action/threshold(服务端 None=不触动,
    // 避免把合并视图值固化成陈旧锚点/清掉既有动作覆盖)
    mutationFn: (input: { name: string; enabled: boolean }) =>
      updateGuardrailRule(input.name, { enabled: input.enabled }),
    onSuccess: onSaved,
    onError: onSaveError,
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
    onSuccess: onSaved,
    onError: onSaveError,
  });

  const total = audit.data?.total ?? 0;
  usePageFallback(audit.data, page, setPage);

  /** 层筛选变化回到第一页。 */
  const changeFtype = (v: string) => {
    setFtype(v);
    setPage(1);
  };
  /** 动作筛选变化回到第一页。 */
  const changeFaction = (v: string) => {
    setFaction(v);
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="护栏"
        description="AI 问答的安全防线:拦截诱导攻击、保护敏感信息、管控高危操作。可调整规则并查看触发记录。"
      />

      {rules.isError && <ErrorAlert />}
      <GuardrailRulesTable
        rows={rules.data?.rules ?? []}
        isPending={rules.isPending}
        editPending={editMeta.isPending}
        togglePending={toggle.isPending}
        onEditMeta={(input) => editMeta.mutate(input)}
        onToggle={(input) => toggle.mutate(input)}
      />
      {toggleError && (
        <p className="text-xs text-destructive" role="alert">
          {toggleError}
        </p>
      )}

      <GuardrailAuditSection
        items={audit.data?.items ?? []}
        total={total}
        page={page}
        totalPages={Math.max(1, Math.ceil(total / PAGE_SIZE))}
        isPending={audit.isPending}
        isError={audit.isError}
        ftype={ftype}
        faction={faction}
        startDate={startDate}
        endDate={endDate}
        onFtype={changeFtype}
        onFaction={changeFaction}
        onDateRange={(s, e) => {
          setStartDate(s);
          setEndDate(e);
          setPage(1);
        }}
        onPageChange={setPage}
      />
    </div>
  );
}
