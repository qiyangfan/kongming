/* 语义层页的数据装配:知识条目候选、关系/指标清单、部门树拍平与写操作。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { components } from "@kongming/api-client/api-types.gen";
import { api } from "@kongming/api-client/api";
import { ragApi } from "@kongming/api-client/rag-api";
import {
  addRelation,
  deleteMetric,
  deleteRelation,
  introspectRelations,
  listMetrics,
  listRelations,
  upsertMetric,
  type MetricItem,
} from "@kongming/api-client/semantic-api";
import type { JoinPair } from "./semantic-page-relations";
import type { MetricForm } from "./semantic-page-metrics";

const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

// 候选表清单=当前数据源知识条目(引用条目外的表后端 400 拒;后端页大小
// 上限 100,全页遍历取全,安全帽 5 页=500 表)
export function useSemanticKnowledge(ds: string, enabled: boolean) {
  return useQuery({
    queryKey: ["semantic", "knowledge", ds],
    queryFn: async () => {
      const first = await ragApi.page({ page: 1, size: 100, datasourceKey: ds });
      const items = [...first.items];
      const pages = Math.min(Math.ceil(first.total / 100), 5);
      for (let p = 2; p <= pages; p++) {
        items.push(...(await ragApi.page({ page: p, size: 100, datasourceKey: ds })).items);
      }
      return items;
    },
    enabled,
  });
}

// DEPT 档归属部门候选(树接口拍平;仅 DEPT 档时拉取)
export function useDeptOptions(enabled: boolean) {
  return useQuery({
    queryKey: ["depts", "tree"],
    queryFn: async () => {
      type DeptNode = components["schemas"]["DeptNodeOut"];
      const tree = await api.get<DeptNode[]>("/system/depts");
      const out: { id: string; name: string }[] = [];
      const walk = (nodes: DeptNode[], prefix: string) => {
        for (const n of nodes) {
          out.push({ id: n.id, name: `${prefix}${n.dept_name}` });
          if (n.children?.length) walk(n.children, `${prefix}${n.dept_name} / `);
        }
      };
      walk(tree, "");
      return out;
    },
    enabled,
  });
}

export function useSemanticLists(ds: string) {
  const relations = useQuery({
    queryKey: ["semantic", "relations", ds],
    queryFn: () => listRelations(ds),
  });
  const metrics = useQuery({
    queryKey: ["semantic", "metrics", ds],
    queryFn: () => listMetrics(ds),
  });
  return { relations, metrics };
}

export type SemanticDeleteTarget = {
  kind: "rel" | "metric";
  id: string;
  name: string;
  error?: string;
};

type Handlers = {
  ds: string;
  onNotice: (m: string | null) => void;
  onError: (m: string | null) => void;
  resetRelForm: () => void;
  resetMetricForm: () => void;
  onDeleteTarget: (t: SemanticDeleteTarget | null) => void;
};

export function useSemanticMutations(h: Handlers) {
  const qc = useQueryClient();
  const invalidate = (kind: "relations" | "metrics") =>
    void qc.invalidateQueries({ queryKey: ["semantic", kind, h.ds] });

  return {
    introspect: useMutation({
      mutationFn: () => introspectRelations(h.ds),
      onSuccess: (d) => {
        h.onNotice(`扫描完成:发现 ${d.relations} 条关系(幂等,手工关系不受影响)`);
        h.onError(null);
        invalidate("relations");
      },
      onError: (e) => h.onError(errText(e, "扫描失败")),
    }),
    addRel: useMutation({
      mutationFn: (v: { srcTable: string; tgtTable: string; pairs: JoinPair[] }) =>
        addRelation({
          datasource_key: h.ds,
          source_table: v.srcTable,
          target_table: v.tgtTable,
          join_keys: v.pairs
            .filter((p) => p.left.trim() && p.right.trim())
            .map((p) => ({ left_col: p.left.trim(), right_col: p.right.trim() })),
        }),
      onSuccess: () => {
        h.resetRelForm();
        h.onError(null);
        invalidate("relations");
      },
      // 服务端错误码转用户文案
      onError: (e) =>
        h.onError(
          e instanceof Error && e.message === "RELATION_TABLE_UNKNOWN"
            ? "引用的表不在知识条目中,请先在知识库扫描/上传该表"
            : errText(e, "保存失败"),
        ),
    }),
    saveMetric: useMutation({
      mutationFn: (form: MetricForm) =>
        upsertMetric({
          datasource_key: h.ds,
          metric_name: form.name.trim(),
          sql_template: form.template.trim(),
          business_terms: form.terms
            .split(/[,，]/)
            .map((t) => t.trim())
            .filter(Boolean),
          enabled: true,
          visible_scope: form.scope,
          dept_id: form.scope === "DEPT" ? form.deptId : null,
        }),
      onSuccess: () => {
        h.resetMetricForm();
        h.onError(null);
        invalidate("metrics");
      },
      onError: (e) => h.onError(errText(e, "保存失败")),
    }),
    toggleMetric: useMutation({
      mutationFn: (input: { m: MetricItem; enabled: boolean }) =>
        upsertMetric({
          datasource_key: h.ds,
          metric_name: input.m.metric_name,
          sql_template: input.m.sql_template,
          business_terms: input.m.business_terms,
          enabled: input.enabled,
          // 启停带原值,不把 DEPT 档重置回 ALL
          visible_scope: input.m.visible_scope,
          dept_id: input.m.dept_id,
        }),
      onSuccess: () => invalidate("metrics"),
      onError: (e) => h.onError(errText(e, "保存失败")),
    }),
    delRel: useMutation({
      mutationFn: (id: string) => deleteRelation(id),
      onSuccess: () => {
        h.onDeleteTarget(null);
        invalidate("relations");
      },
      // 删除失败也在对话框内回显,避免零反馈
      onError: (e) =>
        h.onDeleteTarget({
          kind: "rel",
          id: "",
          name: "",
          error: errText(e, "删除失败"),
        }),
    }),
    delMetric: useMutation({
      mutationFn: (id: string) => deleteMetric(id),
      onSuccess: () => {
        h.onDeleteTarget(null);
        invalidate("metrics");
      },
      onError: (e) =>
        h.onDeleteTarget({
          kind: "metric",
          id: "",
          name: "",
          error: errText(e, "删除失败"),
        }),
    }),
  };
}
