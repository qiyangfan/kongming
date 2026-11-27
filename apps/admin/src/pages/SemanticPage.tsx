/* 管理表关系和指标口径；表关系可查看扫描结果或手动维护。 */

import { useState } from "react";

import { PageHeader } from "@kongming/ui/page-header";
import { ToggleGroup, ToggleGroupItem } from "@kongming/ui/toggle-group";
import { useDatasource } from "@kongming/api-client/datasource-store";
import {
  useDeptOptions,
  useSemanticKnowledge,
  useSemanticLists,
  useSemanticMutations,
  type SemanticDeleteTarget,
} from "./semantic-page-data";
import { SemanticRelationsTab, type JoinPair, type TableOption } from "./semantic-page-relations";
import { SemanticMetricsTab, type MetricForm } from "./semantic-page-metrics";
import { SemanticDeleteDialogs } from "./semantic-page-delete";

const EMPTY_PAIRS: JoinPair[] = [{ left: "", right: "" }];

const EMPTY_METRIC: MetricForm = { name: "", template: "", terms: "", scope: "ALL", deptId: "" };

// 知识条目 → 下拉候选;展示业务别名,无别名时回退 schema.table
function toTableOptions(
  items: { schema_name: string; table_name: string; alias: string }[],
): TableOption[] {
  return items.map((i) => {
    const ref = `${i.schema_name}.${i.table_name}`;
    return { value: ref, label: i.alias ? `${i.alias}(${ref})` : ref };
  });
}

type Tab = "relations" | "metrics";

export default function SemanticPage() {
  const datasource = useDatasource((s) => s.current);
  const ds = datasource ?? "default";
  const [tab, setTab] = useState<Tab>("relations");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // 关系表单:表/列取知识条目候选,join 键为键对行
  const [srcTable, setSrcTable] = useState("");
  const [tgtTable, setTgtTable] = useState("");
  const [joinPairs, setJoinPairs] = useState<JoinPair[]>(EMPTY_PAIRS);
  const [metricForm, setMetricForm] = useState<MetricForm>(EMPTY_METRIC);
  const [delTarget, setDelTarget] = useState<SemanticDeleteTarget | null>(null);

  const knowledge = useSemanticKnowledge(ds, tab === "relations");
  const { relations, metrics } = useSemanticLists(ds);
  // 可见范围 DEPT 档仅注入给归属部门内的调用者
  const depts = useDeptOptions(tab === "metrics" && metricForm.scope === "DEPT");

  const m = useSemanticMutations({
    ds,
    onNotice: setNotice,
    onError: setError,
    resetRelForm: () => {
      setSrcTable("");
      setTgtTable("");
      setJoinPairs(EMPTY_PAIRS);
    },
    resetMetricForm: () => setMetricForm(EMPTY_METRIC),
    onDeleteTarget: setDelTarget,
  });

  const columnsOf = (ref: string) =>
    (knowledge.data ?? [])
      .filter((i) => `${i.schema_name}.${i.table_name}` === ref)
      .flatMap((i) => Object.keys(i.columns_comment));

  // 换表清键对(列集随表变)
  const pickTable = (setter: (v: string) => void) => (v: string) => {
    setter(v);
    setJoinPairs(EMPTY_PAIRS);
  };

  const patchPair = (idx: number, patch: Partial<JoinPair>) =>
    setJoinPairs((ps) => ps.map((p, i) => (i === idx ? { ...p, ...patch } : p)));

  const confirmDelete = (t: SemanticDeleteTarget) => {
    if (t.kind === "rel") m.delRel.mutate(t.id);
    else m.delMetric.mutate(t.id);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="语义层"
        description={`教军师理解业务:表之间的关联关系与业务指标的计算口径,让回答贴合业务含义。当前数据源:${ds}`}
        actions={
          <ToggleGroup type="single" value={tab} onValueChange={(v) => v && setTab(v as Tab)}>
            <ToggleGroupItem value="relations">表关系</ToggleGroupItem>
            <ToggleGroupItem value="metrics">指标口径</ToggleGroupItem>
          </ToggleGroup>
        }
      />

      {notice && (
        <p className="text-xs text-primary" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {tab === "relations" ? (
        <SemanticRelationsTab
          relations={relations}
          knowledge={knowledge}
          tableOptions={toTableOptions(knowledge.data ?? [])}
          columnsOf={columnsOf}
          srcTable={srcTable}
          tgtTable={tgtTable}
          onSrcTableChange={pickTable(setSrcTable)}
          onTgtTableChange={pickTable(setTgtTable)}
          joinPairs={joinPairs}
          onPatchPair={patchPair}
          onRemovePair={(idx) => setJoinPairs((ps) => ps.filter((_, i) => i !== idx))}
          onAddPair={() => setJoinPairs((ps) => [...ps, { left: "", right: "" }])}
          addPending={m.addRel.isPending}
          onAdd={() => m.addRel.mutate({ srcTable, tgtTable, pairs: joinPairs })}
          scanPending={m.introspect.isPending}
          onScan={() => m.introspect.mutate()}
          onDelete={(r) => setDelTarget({ kind: "rel", id: r.id, name: r.source_table })}
        />
      ) : (
        <SemanticMetricsTab
          metrics={metrics}
          form={metricForm}
          onFormChange={setMetricForm}
          depts={depts}
          savePending={m.saveMetric.isPending}
          onSave={() => m.saveMetric.mutate(metricForm)}
          togglePending={m.toggleMetric.isPending}
          onToggle={(metric, enabled) => m.toggleMetric.mutate({ m: metric, enabled })}
          onDelete={(metric) =>
            setDelTarget({ kind: "metric", id: metric.id, name: metric.metric_name })
          }
        />
      )}

      <SemanticDeleteDialogs
        target={delTarget}
        pending={m.delRel.isPending || m.delMetric.isPending}
        onChange={setDelTarget}
        onConfirm={confirmDelete}
      />
    </div>
  );
}
