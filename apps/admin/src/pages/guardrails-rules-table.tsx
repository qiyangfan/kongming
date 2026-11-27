/* 护栏规则表:可调动作与阈值、启停开关。 */

import { ShieldCheck } from "lucide-react";

import { Badge } from "@kongming/ui/badge";
import { Input } from "@kongming/ui/input";
import { StatusBadge } from "@kongming/ui/status-badge";
import { DataView } from "@kongming/ui/data-view";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { Switch } from "@kongming/ui/switch";

import { ACTION_META, TYPE_LABELS } from "./guardrail-labels";

interface GuardrailRule {
  name: string;
  guardrail_type: string;
  action: string;
  maskable: boolean;
  description: string;
  threshold: number | null;
  enabled: boolean;
  overridden: boolean;
}

interface GuardrailRulesTableProps {
  rows: GuardrailRule[];
  isPending: boolean;
  editPending: boolean;
  togglePending: boolean;
  onEditMeta: (input: {
    name: string;
    enabled: boolean;
    action?: "blocked" | "masked";
    threshold?: number;
  }) => void;
  onToggle: (input: { name: string; enabled: boolean }) => void;
}

/** 可脱敏规则的动作下拉。 */
function ActionSelect({
  row,
  disabled,
  onEditMeta,
}: {
  row: GuardrailRule;
  disabled: boolean;
  onEditMeta: GuardrailRulesTableProps["onEditMeta"];
}) {
  return (
    <Select
      value={row.action}
      onValueChange={(v) =>
        onEditMeta({ name: row.name, enabled: row.enabled, action: v as "blocked" | "masked" })
      }
      disabled={disabled}
    >
      <SelectTrigger size="sm" className="w-24" aria-label={`${row.name} 动作`}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="blocked">拦截</SelectItem>
        <SelectItem value="masked">脱敏</SelectItem>
      </SelectContent>
    </Select>
  );
}

/** 工具意图层的阈值输入;失焦时校验 1–100 并提交。 */
function ThresholdInput({
  row,
  disabled,
  onEditMeta,
}: {
  row: GuardrailRule;
  disabled: boolean;
  onEditMeta: GuardrailRulesTableProps["onEditMeta"];
}) {
  if (row.guardrail_type !== "tool_intent") return <>—</>;
  return (
    <Input
      type="number"
      min={1}
      max={100}
      defaultValue={row.threshold ?? 1}
      size="sm"
      className="w-20"
      aria-label={`${row.name} 阈值`}
      disabled={disabled}
      onBlur={(e) => {
        const v = Number.parseInt(e.target.value, 10);
        if (Number.isFinite(v) && v >= 1 && v <= 100 && v !== row.threshold) {
          onEditMeta({ name: row.name, enabled: row.enabled, threshold: v });
        }
      }}
    />
  );
}

/** 规则名 + 已调整标记。 */
function RuleNameCell({ row }: { row: GuardrailRule }) {
  return (
    <span className="flex items-center gap-1.5">
      <ShieldCheck className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="truncate" title={row.name}>
        {row.name}
      </span>
      {row.overridden && (
        <Badge variant="outline" className="shrink-0">
          已调整
        </Badge>
      )}
    </span>
  );
}

/** 护栏规则清单(只读展示 + 可调动作/阈值/启停)。 */
export function GuardrailRulesTable(props: GuardrailRulesTableProps) {
  const { rows, isPending, editPending, togglePending, onEditMeta, onToggle } = props;
  return (
    <DataView
      rows={rows}
      rowKey={(r) => r.name}
      isLoading={isPending}
      isEmpty={!isPending && rows.length === 0}
      emptyText="暂无护栏规则"
      columns={[
        {
          key: "name",
          header: "规则",
          cardPriority: 1,
          cellClassName: "font-mono text-xs font-medium",
          render: (r) => <RuleNameCell row={r} />,
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
              <ActionSelect row={r} disabled={editPending} onEditMeta={onEditMeta} />
            ) : (
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
          render: (r) => <ThresholdInput row={r} disabled={editPending} onEditMeta={onEditMeta} />,
        },
        {
          key: "enabled",
          header: "启用",
          cardPriority: 4,
          render: (r) => (
            <Switch
              checked={r.enabled}
              disabled={togglePending}
              onCheckedChange={(on) => onToggle({ name: r.name, enabled: on })}
              aria-label={`启用 ${r.name}`}
            />
          ),
        },
      ]}
    />
  );
}
