/* 语义层页(023,semantic-layer spec「语义层管理与权限」):
   表关系(introspection 只读/manual 可增删+触发扫描)与指标口径(增删/
   启停)双 Tab;权限点 system:semantic:manage。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Loader2, Plus, RefreshCw, X } from "lucide-react";

import type { components } from "@/lib/api-types.gen";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/page-header";
import { DataView } from "@/components/data-view";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ErrorAlert } from "@/components/error-alert";
import { useDatasource } from "@/lib/datasource-store";
import { api } from "@/lib/api";
import { ragApi } from "@/lib/rag-api";
import {
  addRelation,
  deleteMetric,
  deleteRelation,
  introspectRelations,
  listMetrics,
  listRelations,
  upsertMetric,
} from "@/lib/semantic-api";

export default function SemanticPage() {
  const qc = useQueryClient();
  const datasource = useDatasource((s) => s.current);
  const ds = datasource ?? "default";
  const [tab, setTab] = useState<"relations" | "metrics">("relations");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // 关系表单(040:表/列改知识条目候选下拉;join 键改键对行,替代手输「左=右」CSV)
  const [srcTable, setSrcTable] = useState("");
  const [tgtTable, setTgtTable] = useState("");
  const [joinPairs, setJoinPairs] = useState<{ left: string; right: string }[]>([
    { left: "", right: "" },
  ]);
  // 指标表单
  const [mName, setMName] = useState("");
  const [mTpl, setMTpl] = useState("");
  const [mTerms, setMTerms] = useState("");
  // 110:可见范围——DEPT 档仅归属部门集合内调用者注入
  const [mScope, setMScope] = useState<"ALL" | "DEPT">("ALL");
  const [mDeptId, setMDeptId] = useState("");

  // 040:候选表清单=当前数据源知识条目(引用条目外的表后端 400 拒;后端页大小
  // 上限 100,全页遍历取全,安全帽 5 页=500 表)
  const knowledge = useQuery({
    queryKey: ["semantic", "knowledge", ds],
    queryFn: async () => {
      const first = await ragApi.page(1, 100, ds);
      const items = [...first.items];
      const pages = Math.min(Math.ceil(first.total / 100), 5);
      for (let p = 2; p <= pages; p++) {
        items.push(...(await ragApi.page(p, 100, ds)).items);
      }
      return items;
    },
    enabled: tab === "relations",
  });
  const tableOptions = (knowledge.data ?? []).map((i) => ({
    value: `${i.schema_name}.${i.table_name}`,
    label: i.alias
      ? `${i.alias}(${i.schema_name}.${i.table_name})`
      : `${i.schema_name}.${i.table_name}`,
  }));
  const columnsOf = (ref: string) =>
    (knowledge.data ?? [])
      .filter((i) => `${i.schema_name}.${i.table_name}` === ref)
      .flatMap((i) => Object.keys(i.columns_comment));

  const relations = useQuery({
    queryKey: ["semantic", "relations", ds],
    queryFn: () => listRelations(ds),
  });
  const metrics = useQuery({
    queryKey: ["semantic", "metrics", ds],
    queryFn: () => listMetrics(ds),
  });
  // 110:DEPT 档归属部门候选(树接口拍平;仅 DEPT 档时拉取)
  const depts = useQuery({
    queryKey: ["depts", "tree"],
    queryFn: async () => {
      // 生成契约类型(宪法⑤:禁手写 API 类型;评审修复)
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
    enabled: tab === "metrics" && mScope === "DEPT",
  });

  const introspect = useMutation({
    mutationFn: () => introspectRelations(ds),
    onSuccess: (d) => {
      setNotice(`扫描完成:发现 ${d.relations} 条关系(幂等,手工关系不受影响)`);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "relations", ds] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "扫描失败"),
  });

  const addRel = useMutation({
    mutationFn: () =>
      addRelation({
        datasource_key: ds,
        source_table: srcTable,
        target_table: tgtTable,
        join_keys: joinPairs
          .filter((p) => p.left.trim() && p.right.trim())
          .map((p) => ({ left_col: p.left.trim(), right_col: p.right.trim() })),
      }),
    onSuccess: () => {
      setSrcTable("");
      setTgtTable("");
      setJoinPairs([{ left: "", right: "" }]);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "relations", ds] });
    },
    onError: (e) => {
      // 040 写边界闸 detail 映射(黑话禁入用户文案)
      const reason =
        e.message === "RELATION_TABLE_UNKNOWN"
          ? "引用的表不在知识条目中,请先在知识库扫描/上传该表"
          : e.message;
      setError(e instanceof Error ? reason : "保存失败");
    },
  });

  const saveMetric = useMutation({
    mutationFn: () =>
      upsertMetric({
        datasource_key: ds,
        metric_name: mName.trim(),
        sql_template: mTpl.trim(),
        business_terms: mTerms
          .split(/[,，]/)
          .map((t) => t.trim())
          .filter(Boolean),
        enabled: true,
        visible_scope: mScope,
        dept_id: mScope === "DEPT" ? mDeptId : null,
      }),
    onSuccess: () => {
      setMName("");
      setMTpl("");
      setMTerms("");
      setMScope("ALL");
      setMDeptId("");
      setError(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "metrics", ds] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "保存失败"),
  });

  const toggleMetric = useMutation({
    mutationFn: (input: { m: import("@/lib/semantic-api").MetricItem; enabled: boolean }) =>
      upsertMetric({
        datasource_key: ds,
        metric_name: input.m.metric_name,
        sql_template: input.m.sql_template,
        business_terms: input.m.business_terms,
        enabled: input.enabled,
        // 110:启停带原值,不把 DEPT 档重置回 ALL
        visible_scope: input.m.visible_scope,
        dept_id: input.m.dept_id,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["semantic", "metrics", ds] }),
    onError: (e) => setError(e instanceof Error ? e.message : "保存失败"),
  });

  const delRel = useMutation({
    mutationFn: (id: string) => deleteRelation(id),
    onSuccess: () => {
      setDelTarget(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "relations", ds] });
    },
    // 审查循环 F3:失败零反馈=用户无从得知未删成;对话框内回显
    onError: (e) =>
      setDelTarget({
        kind: "rel",
        id: "",
        name: "",
        error: e instanceof Error ? e.message : "删除失败",
      }),
  });
  const delMetric = useMutation({
    mutationFn: (id: string) => deleteMetric(id),
    onSuccess: () => {
      setDelTarget(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "metrics", ds] });
    },
    onError: (e) =>
      setDelTarget({
        kind: "metric",
        id: "",
        name: "",
        error: e instanceof Error ? e.message : "删除失败",
      }),
  });
  const [delTarget, setDelTarget] = useState<{
    kind: "rel" | "metric";
    id: string;
    name: string;
    error?: string;
  } | null>(null);

  return (
    <div className="space-y-4">
      <PageHeader
        title="语义层"
        description={`教军师理解业务:表之间的关联关系与业务指标的计算口径,让回答贴合业务含义。当前数据源:${ds}`}
        actions={
          <ToggleGroup
            type="single"
            value={tab}
            onValueChange={(v) => v && setTab(v as "relations" | "metrics")}
          >
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
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">表关系</h3>
            <Button
              size="sm"
              variant="outline"
              disabled={introspect.isPending}
              onClick={() => introspect.mutate()}
            >
              {introspect.isPending ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="size-3.5" aria-hidden />
              )}
              扫描表关联
            </Button>
          </div>
          {relations.isError && <ErrorAlert />}
          <DataView
            rows={relations.data ?? []}
            rowKey={(r) => r.id}
            isLoading={relations.isPending}
            isEmpty={!relations.isPending && relations.data?.length === 0}
            emptyText="暂无关系——点「扫描表关联」或手工新增"
            columns={[
              {
                key: "source_table",
                header: "左表",
                cardPriority: 1,
                cellClassName: "font-mono text-xs",
                render: (r) => (
                  <span
                    className="block max-w-48 truncate font-mono text-xs"
                    title={r.source_table}
                  >
                    {r.source_table}
                  </span>
                ),
              },
              {
                key: "target_table",
                header: "右表",
                cardPriority: 2,
                cellClassName: "font-mono text-xs",
                render: (r) => (
                  <span
                    className="block max-w-48 truncate font-mono text-xs"
                    title={r.target_table}
                  >
                    {r.target_table}
                  </span>
                ),
              },
              {
                key: "join_keys",
                header: "关联字段",
                cardPriority: 3,
                cellClassName: "font-mono text-xs",
                render: (r) => (
                  <span
                    className="block max-w-40 truncate font-mono text-xs"
                    title={
                      r.join_keys.map((k) => `${k.left_col}=${k.right_col}`).join(", ") || undefined
                    }
                  >
                    {r.join_keys.map((k) => `${k.left_col}=${k.right_col}`).join(", ")}
                  </span>
                ),
              },
              {
                key: "source",
                header: "来源",
                cardPriority: 4,
                render: (r) => (
                  <Badge variant={r.source === "manual" ? "default" : "secondary"}>
                    {r.source === "manual" ? "手工" : "自动扫描"}
                  </Badge>
                ),
              },
            ]}
            actions={[
              {
                label: "删除",
                destructive: true,
                // R1-L4:仅手工行渲染删除(introspection 行只读)
                isVisible: (r) => r.source === "manual",
                onSelect: (r) => {
                  setDelTarget({ kind: "rel", id: r.id, name: r.source_table });
                },
              },
            ]}
          />
          <div className="space-y-2 rounded-lg border border-border p-3">
            {knowledge.isError && (
              <p className="text-xs text-destructive" role="alert">
                知识条目加载失败,无法选择关联表,请刷新重试
              </p>
            )}
            <div className="flex flex-wrap items-end gap-3">
              {/* 040:表引用改知识条目下拉(展示业务别名,候选与后端存在性闸同源) */}
              <div className="space-y-1">
                <Label className="text-xs">左表(知识条目)</Label>
                <Select
                  value={srcTable}
                  onValueChange={(v) => {
                    setSrcTable(v);
                    setJoinPairs([{ left: "", right: "" }]); // 换表清键对(列集随表变)
                  }}
                  disabled={knowledge.isPending}
                >
                  <SelectTrigger aria-label="左表" className="w-56">
                    <SelectValue
                      placeholder={
                        knowledge.isError
                          ? "候选加载失败"
                          : knowledge.isPending
                            ? "加载中…"
                            : tableOptions.length
                              ? "选择左表"
                              : "暂无知识条目"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {tableOptions.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">右表(知识条目)</Label>
                <Select
                  value={tgtTable}
                  onValueChange={(v) => {
                    setTgtTable(v);
                    setJoinPairs([{ left: "", right: "" }]);
                  }}
                  disabled={knowledge.isPending}
                >
                  <SelectTrigger aria-label="右表" className="w-56">
                    <SelectValue
                      placeholder={
                        knowledge.isError
                          ? "候选加载失败"
                          : knowledge.isPending
                            ? "加载中…"
                            : tableOptions.length
                              ? "选择右表"
                              : "暂无知识条目"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {tableOptions.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                size="sm"
                disabled={
                  !srcTable ||
                  !tgtTable ||
                  !joinPairs.some((p) => p.left.trim() && p.right.trim()) ||
                  addRel.isPending
                }
                onClick={() => addRel.mutate()}
              >
                <Plus className="size-3.5" aria-hidden />
                新增关系
              </Button>
            </div>
            {/* JOIN 键对行(多列复合键=多行);表条目缺列清单时该侧降级手输 */}
            {srcTable && tgtTable && (
              <div className="space-y-2">
                {joinPairs.map((pair, idx) => {
                  const srcCols = columnsOf(srcTable);
                  const tgtCols = columnsOf(tgtTable);
                  return (
                    <div key={idx} className="flex items-center gap-2">
                      <span className="w-14 text-xs text-muted-foreground">
                        {idx === 0 ? "关联字段" : `字段 ${idx + 1}`}
                      </span>
                      {srcCols.length > 0 ? (
                        <Select
                          value={pair.left}
                          onValueChange={(v) =>
                            setJoinPairs((ps) =>
                              ps.map((p, i) => (i === idx ? { ...p, left: v } : p)),
                            )
                          }
                        >
                          <SelectTrigger aria-label={`左列 ${idx + 1}`} className="w-44">
                            <SelectValue placeholder="左表列" />
                          </SelectTrigger>
                          <SelectContent>
                            {srcCols.map((c) => (
                              <SelectItem key={c} value={c}>
                                {c}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Input
                          aria-label={`左列 ${idx + 1}`}
                          value={pair.left}
                          onChange={(e) =>
                            setJoinPairs((ps) =>
                              ps.map((p, i) => (i === idx ? { ...p, left: e.target.value } : p)),
                            )
                          }
                          className="w-44"
                          placeholder="左表列(条目缺列清单,手输)"
                        />
                      )}
                      <span className="text-xs text-muted-foreground">=</span>
                      {tgtCols.length > 0 ? (
                        <Select
                          value={pair.right}
                          onValueChange={(v) =>
                            setJoinPairs((ps) =>
                              ps.map((p, i) => (i === idx ? { ...p, right: v } : p)),
                            )
                          }
                        >
                          <SelectTrigger aria-label={`右列 ${idx + 1}`} className="w-44">
                            <SelectValue placeholder="右表列" />
                          </SelectTrigger>
                          <SelectContent>
                            {tgtCols.map((c) => (
                              <SelectItem key={c} value={c}>
                                {c}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Input
                          aria-label={`右列 ${idx + 1}`}
                          value={pair.right}
                          onChange={(e) =>
                            setJoinPairs((ps) =>
                              ps.map((p, i) => (i === idx ? { ...p, right: e.target.value } : p)),
                            )
                          }
                          className="w-44"
                          placeholder="右表列(条目缺列清单,手输)"
                        />
                      )}
                      {joinPairs.length > 1 && (
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`移除键对 ${idx + 1}`}
                          onClick={() => setJoinPairs((ps) => ps.filter((_, i) => i !== idx))}
                        >
                          <X className="size-3.5" aria-hidden />
                        </Button>
                      )}
                    </div>
                  );
                })}
                <div>
                  <Button
                    size="xs"
                    variant="outline"
                    onClick={() => setJoinPairs((ps) => [...ps, { left: "", right: "" }])}
                  >
                    <Plus className="size-3.5" aria-hidden />
                    添加关联字段(多字段组合)
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
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
                  <span
                    className="block max-w-32 truncate text-sm font-medium"
                    title={m.metric_name}
                  >
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
                  <Badge variant="secondary">
                    {m.visible_scope === "DEPT" ? "限部门" : "公共"}
                  </Badge>
                ),
              },
              {
                key: "enabled",
                header: "状态",
                cardPriority: 2,
                render: (m) => (
                  // R1-L4:启停切换(upsert 带原值,不重置其他字段)
                  <Switch
                    checked={m.enabled}
                    disabled={toggleMetric.isPending}
                    onCheckedChange={(on) => toggleMetric.mutate({ m, enabled: on })}
                    aria-label={`启停 ${m.metric_name}`}
                  />
                ),
              },
            ]}
            actions={[
              {
                label: "删除",
                destructive: true,
                onSelect: (m) => setDelTarget({ kind: "metric", id: m.id, name: m.metric_name }),
              },
            ]}
          />
          <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border p-3">
            <div className="space-y-1">
              <Label className="text-xs">指标名</Label>
              <Input
                value={mName}
                onChange={(e) => setMName(e.target.value)}
                className="w-40"
                placeholder="复购率"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">计算口径(SQL 片段,供 AI 参考)</Label>
              <Input
                value={mTpl}
                onChange={(e) => setMTpl(e.target.value)}
                className="w-96"
                placeholder="COUNT(DISTINCT customer_id) / ..."
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">触发词(逗号分隔)</Label>
              <Input
                value={mTerms}
                onChange={(e) => setMTerms(e.target.value)}
                className="w-48"
                placeholder="复购,回头率"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">可见范围</Label>
              <Select
                value={mScope}
                onValueChange={(v) => {
                  setMScope(v as "ALL" | "DEPT");
                  setMDeptId("");
                }}
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
            {mScope === "DEPT" && (
              <div className="space-y-1">
                <Label className="text-xs">归属部门</Label>
                <Select value={mDeptId} onValueChange={setMDeptId}>
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
            <Button
              size="sm"
              disabled={
                !mName.trim() ||
                !mTpl.trim() ||
                (mScope === "DEPT" && !mDeptId) ||
                saveMetric.isPending
              }
              onClick={() => saveMetric.mutate()}
            >
              <Layers className="size-3.5" aria-hidden />
              保存指标
            </Button>
          </div>
        </div>
      )}

      {/* 审查循环 F3:删除受控确认(全站同款)+失败行内可见 */}
      <AlertDialog
        open={delTarget !== null && delTarget.id !== ""}
        onOpenChange={(open) => {
          if (!open) setDelTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              删除{delTarget?.kind === "rel" ? "表关系" : "指标"}「{delTarget?.name}」?
            </AlertDialogTitle>
            <AlertDialogDescription>
              删除后,AI 生成回答时不再参考该{delTarget?.kind === "rel" ? "表关联" : "指标口径"}。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {delTarget?.error && (
            <p className="text-sm text-destructive" role="alert">
              {delTarget.error}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={delRel.isPending || delMetric.isPending}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={delRel.isPending || delMetric.isPending}
              onClick={() => {
                if (!delTarget || !delTarget.id) return;
                if (delTarget.kind === "rel") delRel.mutate(delTarget.id);
                else delMetric.mutate(delTarget.id);
              }}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 失败回显兜留框(错误时 id 已清,靠 open 语义保持可见) */}
      <AlertDialog
        open={delTarget !== null && delTarget.id === "" && !!delTarget.error}
        onOpenChange={(open) => {
          if (!open) setDelTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除失败</AlertDialogTitle>
            <AlertDialogDescription>{delTarget?.error}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>知道了</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
