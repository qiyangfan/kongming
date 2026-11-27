/* 语义层「指标口径」页签:指标清单(启停/删除)与新增表单(含可见范围)。 */

import { Layers } from "lucide-react";

import { Badge } from "@kongming/ui/badge";
import { Button } from "@kongming/ui/button";
import { Input } from "@kongming/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { Switch } from "@kongming/ui/switch";
import { Label } from "@kongming/ui/label";
import { DataView } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import type { MetricItem } from "@kongming/api-client/semantic-api";

type QueryLike<T> = { data: T | undefined; isPending: boolean; isError: boolean };

export type MetricForm = {
  name: string;
  template: string;
  terms: string;
  scope: "ALL" | "DEPT";
  deptId: string;
};

type DeptOption = { id: string; name: string };

function MetricFields({
  form,
  onChange,
  depts,
}: {
  form: MetricForm;
  onChange: (f: MetricForm) => void;
  depts: QueryLike<DeptOption[]>;
}) {
  return (
    <>
      <div className="space-y-1">
        <Label className="text-xs">指标名</Label>
        <Input
          value={form.name}
          onChange={(e) => onChange({ ...form, name: e.target.value })}
          className="w-40"
          placeholder="复购率"
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs">计算口径(SQL 片段,供 AI 参考)</Label>
        <Input
          value={form.template}
          onChange={(e) => onChange({ ...form, template: e.target.value })}
          className="w-96"
          placeholder="COUNT(DISTINCT customer_id) / ..."
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs">触发词(逗号分隔)</Label>
        <Input
          value={form.terms}
          onChange={(e) => onChange({ ...form, terms: e.target.value })}
          className="w-48"
          placeholder="复购,回头率"
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs">可见范围</Label>
        {/* 切档即清部门,防 DEPT→ALL 后残留他部门 id */}
        <Select
          value={form.scope}
          onValueChange={(v) => onChange({ ...form, scope: v as "ALL" | "DEPT", deptId: "" })}
        >
          <SelectTrigger className="w-28" aria-label="可见范围">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">公共(全员)</SelectItem>
            <SelectItem value="DEPT">限部门</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {form.scope === "DEPT" && (
        <div className="space-y-1">
          <Label className="text-xs">归属部门</Label>
          <Select value={form.deptId} onValueChange={(v) => onChange({ ...form, deptId: v })}>
            <SelectTrigger className="w-44" aria-label="归属部门">
              <SelectValue placeholder="选择部门" />
            </SelectTrigger>
            <SelectContent>
              {(depts.data ?? []).map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </>
  );
}

export function SemanticMetricsTab({
  metrics,
  form,
  onFormChange,
  depts,
  savePending,
  onSave,
  togglePending,
  onToggle,
  onDelete,
}: {
  metrics: QueryLike<MetricItem[]>;
  form: MetricForm;
  onFormChange: (f: MetricForm) => void;
  depts: QueryLike<DeptOption[]>;
  savePending: boolean;
  onSave: () => void;
  togglePending: boolean;
  onToggle: (m: MetricItem, enabled: boolean) => void;
  onDelete: (m: MetricItem) => void;
}) {
  const disabled =
    !form.name.trim() ||
    !form.template.trim() ||
    (form.scope === "DEPT" && !form.deptId) ||
    savePending;
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">指标口径</h3>
      {metrics.isError && <ErrorAlert />}
      <DataView
        rows={metrics.data ?? []}
        rowKey={(m) => m.id}
        isLoading={metrics.isPending}
        isEmpty={!metrics.isPending && metrics.data?.length === 0}
        emptyText="暂无指标——在下方新增"
        columns={[
          {
            key: "metric_name",
            header: "指标",
            cardPriority: 1,
            cellClassName: "text-sm font-medium",
            render: (m) => (
              <span className="block max-w-32 truncate text-sm font-medium" title={m.metric_name}>
                {m.metric_name}
              </span>
            ),
          },
          {
            key: "sql_template",
            header: "口径(仅供 AI 参考,不会执行)",
            cellClassName: "font-mono text-xs text-muted-foreground",
            render: (m) => (
              <span className="block max-w-md truncate" title={m.sql_template}>
                {m.sql_template}
              </span>
            ),
          },
          {
            key: "business_terms",
            header: "触发词",
            cardPriority: 3,
            cellClassName: "text-xs",
            render: (m) => (
              <span
                className="block max-w-32 truncate"
                title={m.business_terms.join(", ") || undefined}
              >
                {m.business_terms.join(", ")}
              </span>
            ),
          },
          {
            key: "visible_scope",
            header: "可见范围",
            cardPriority: 3,
            cellClassName: "text-xs",
            render: (m) => (
              <Badge variant="secondary">{m.visible_scope === "DEPT" ? "限部门" : "公共"}</Badge>
            ),
          },
          {
            key: "enabled",
            header: "状态",
            cardPriority: 2,
            render: (m) => (
              <Switch
                checked={m.enabled}
                disabled={togglePending}
                onCheckedChange={(on) => onToggle(m, on)}
                aria-label={`启停 ${m.metric_name}`}
              />
            ),
          },
        ]}
        actions={[{ label: "删除", destructive: true, onSelect: (m) => onDelete(m) }]}
      />
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border p-3">
        <MetricFields form={form} onChange={onFormChange} depts={depts} />
        <Button size="sm" disabled={disabled} onClick={onSave}>
          <Layers className="size-3.5" aria-hidden />
          保存指标
        </Button>
      </div>
    </div>
  );
}
