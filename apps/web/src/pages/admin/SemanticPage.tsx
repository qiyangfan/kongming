/* 语义层页(023,semantic-layer spec「语义层管理与权限」):
   表关系(introspection 只读/manual 可增删+触发抓取)与指标口径(增删/
   启停)双 Tab;权限点 system:semantic:manage。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";

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
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { QueryErrorAlert } from "@/components/query-error";
import { useDatasource } from "@/lib/datasource-store";
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
  // 关系表单
  const [srcTable, setSrcTable] = useState("");
  const [tgtTable, setTgtTable] = useState("");
  const [joinCol, setJoinCol] = useState("");
  // 指标表单
  const [mName, setMName] = useState("");
  const [mTpl, setMTpl] = useState("");
  const [mTerms, setMTerms] = useState("");

  const relations = useQuery({
    queryKey: ["semantic", "relations", ds],
    queryFn: () => listRelations(ds),
  });
  const metrics = useQuery({
    queryKey: ["semantic", "metrics", ds],
    queryFn: () => listMetrics(ds),
  });

  const introspect = useMutation({
    mutationFn: () => introspectRelations(ds),
    onSuccess: (d) => {
      setNotice(`抓取完成:${d.relations} 条关系(幂等,手工关系不受影响)`);
      setError(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "relations", ds] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "抓取失败"),
  });

  const addRel = useMutation({
    mutationFn: () =>
      addRelation({
        datasource_key: ds,
        source_table: srcTable.trim(),
        target_table: tgtTable.trim(),
        join_keys: joinCol
          .split(",")
          .map((pair) => pair.split("="))
          .filter((kv) => kv.length === 2 && kv[0].trim() && kv[1].trim())
          .map((kv) => ({ left_col: kv[0].trim(), right_col: kv[1].trim() })),
      }),
    onSuccess: () => {
      setSrcTable("");
      setTgtTable("");
      setJoinCol("");
      setError(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "relations", ds] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "保存失败"),
  });

  const saveMetric = useMutation({
    mutationFn: () =>
      upsertMetric({
        datasource_key: ds,
        metric_name: mName.trim(),
        sql_template: mTpl.trim(),
        business_terms: mTerms.split(/[,，]/).map((t) => t.trim()).filter(Boolean),
        enabled: true,
      }),
    onSuccess: () => {
      setMName("");
      setMTpl("");
      setMTerms("");
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
    onError: (e) => setDelTarget({ kind: "rel", id: "", name: "", error: e instanceof Error ? e.message : "删除失败" }),
  });
  const delMetric = useMutation({
    mutationFn: (id: string) => deleteMetric(id),
    onSuccess: () => {
      setDelTarget(null);
      void qc.invalidateQueries({ queryKey: ["semantic", "metrics", ds] });
    },
    onError: (e) => setDelTarget({ kind: "metric", id: "", name: "", error: e instanceof Error ? e.message : "删除失败" }),
  });
  const [delTarget, setDelTarget] = useState<{
    kind: "rel" | "metric";
    id: string;
    name: string;
    error?: string;
  } | null>(null);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">语义层</h2>
          <p className="text-sm text-muted-foreground">
            表关系(JOIN path)与指标口径,注入生成链路;当前数据源:{ds}
          </p>
        </div>
        <ToggleGroup
          type="single"
          value={tab}
          onValueChange={(v) => v && setTab(v as "relations" | "metrics")}
        >
          <ToggleGroupItem value="relations">表关系</ToggleGroupItem>
          <ToggleGroupItem value="metrics">指标口径</ToggleGroupItem>
        </ToggleGroup>
      </div>

      {notice && <p className="text-xs text-primary" role="status">{notice}</p>}
      {error && <p className="text-xs text-destructive" role="alert">{error}</p>}

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
              抓取外键关系
            </Button>
          </div>
          <div className="rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>左表</TableHead>
                  <TableHead>右表</TableHead>
                  <TableHead>JOIN 键</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead className="w-16">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {relations.isError ? (
                  <TableRow>
                    <TableCell colSpan={5}>
                      <QueryErrorAlert />
                    </TableCell>
                  </TableRow>
                ) : relations.isLoading ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-6 text-center">
                      <Loader2 className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
                    </TableCell>
                  </TableRow>
                ) : (relations.data?.length ?? 0) === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-6 text-center text-sm text-muted-foreground">
                      暂无关系——点「抓取外键关系」或手工新增
                    </TableCell>
                  </TableRow>
                ) : (
                  relations.data?.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="font-mono text-xs">{r.source_table}</TableCell>
                      <TableCell className="font-mono text-xs">{r.target_table}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {r.join_keys.map((k) => `${k.left_col}=${k.right_col}`).join(", ")}
                      </TableCell>
                      <TableCell>
                        <Badge variant={r.source === "manual" ? "default" : "secondary"}>
                          {r.source === "manual" ? "手工" : "内省"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {/* R1-L4:introspection 行只读(proposal 立约),仅手工行可删 */}
                        {r.source === "manual" && (
                          <Button
                            size="icon"
                            variant="ghost"
                            disabled={delRel.isPending}
                            onClick={() =>
                              setDelTarget({ kind: "rel", id: r.id, name: r.source_table })
                            }
                            aria-label="删除关系"
                          >
                            <Trash2 className="size-3.5" aria-hidden />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border p-3">
            <div className="space-y-1">
              <Label className="text-xs">左表(schema.table)</Label>
              <Input value={srcTable} onChange={(e) => setSrcTable(e.target.value)} className="w-52" placeholder="tenant_x.orders" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">右表</Label>
              <Input value={tgtTable} onChange={(e) => setTgtTable(e.target.value)} className="w-52" placeholder="tenant_x.customers" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">JOIN 键(左=右)</Label>
              <Input value={joinCol} onChange={(e) => setJoinCol(e.target.value)} className="w-52" placeholder="customer_id=id" />
            </div>
            <Button
              size="sm"
              disabled={!srcTable.trim() || !tgtTable.trim() || !joinCol.includes("=") || addRel.isPending}
              onClick={() => addRel.mutate()}
            >
              <Plus className="size-3.5" aria-hidden />
              新增关系
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold">指标口径</h3>
          <div className="rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>指标</TableHead>
                  <TableHead>口径(仅 prompt 参考,不会执行)</TableHead>
                  <TableHead>触发词</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="w-16">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {metrics.isError ? (
                  <TableRow>
                    <TableCell colSpan={5}>
                      <QueryErrorAlert />
                    </TableCell>
                  </TableRow>
                ) : metrics.isLoading ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-6 text-center">
                      <Loader2 className="mx-auto size-4 animate-spin text-muted-foreground" aria-hidden />
                    </TableCell>
                  </TableRow>
                ) : (metrics.data?.length ?? 0) === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-6 text-center text-sm text-muted-foreground">
                      暂无指标——在下方新增
                    </TableCell>
                  </TableRow>
                ) : (
                  metrics.data?.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="text-sm font-medium">{m.metric_name}</TableCell>
                      <TableCell className="max-w-md font-mono text-xs text-muted-foreground">
                        {m.sql_template}
                      </TableCell>
                      <TableCell className="text-xs">{m.business_terms.join(", ")}</TableCell>
                      <TableCell>
                        {/* R1-L4:启停切换(upsert 带原值,不重置其他字段) */}
                        <Switch
                          checked={m.enabled}
                          disabled={toggleMetric.isPending}
                          onCheckedChange={(on) => toggleMetric.mutate({ m, enabled: on })}
                          aria-label={`启停 ${m.metric_name}`}
                        />
                      </TableCell>
                      <TableCell>
                        <Button
                          size="icon"
                          variant="ghost"
                          disabled={delMetric.isPending}
                          onClick={() =>
                            setDelTarget({ kind: "metric", id: m.id, name: m.metric_name })
                          }
                          aria-label="删除指标"
                        >
                          <Trash2 className="size-3.5" aria-hidden />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border p-3">
            <div className="space-y-1">
              <Label className="text-xs">指标名</Label>
              <Input value={mName} onChange={(e) => setMName(e.target.value)} className="w-40" placeholder="复购率" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">口径 SQL 片段(仅 prompt 注入)</Label>
              <Input value={mTpl} onChange={(e) => setMTpl(e.target.value)} className="w-96" placeholder="COUNT(DISTINCT customer_id) / ..." />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">触发词(逗号分隔)</Label>
              <Input value={mTerms} onChange={(e) => setMTerms(e.target.value)} className="w-48" placeholder="复购,回头率" />
            </div>
            <Button
              size="sm"
              disabled={!mName.trim() || !mTpl.trim() || saveMetric.isPending}
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
              删除后生成链路不再注入该{delTarget?.kind === "rel" ? "JOIN 关系" : "指标口径"}。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {delTarget?.error && (
            <p className="text-sm text-destructive" role="alert">{delTarget.error}</p>
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
