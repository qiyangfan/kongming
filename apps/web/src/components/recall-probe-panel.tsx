/* 召回试跑面板(097):知识库管理页内只读验证面——上传表说明后验证可召回性
   与召回闸门判定观察同页闭环。数据源选项复用页面既有 options 查询(props 注入,
   不重复请求);试跑为显式触发(不自动打端点),进行中禁重复提交。
   渲染如实:零命中/整路被裁/零知识旁路/扩展未开启与零扩展全部明示,不美化。
   端点零副作用(不落库/不失效缓存),面板不提供任何写操作入口。
   「生效阈值」不在试跑响应契约内(service 只透出判定归因与分值观测),
   面板如实展示 verdict 观测值,不猜测阈值。 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { DataView, type DataViewColumn } from "@/components/data-view";
import { EmptyState } from "@/components/empty-state";
import { ErrorAlert } from "@/components/error-alert";
import { StatusBadge, type StatusMeta } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { DatasourceOptionOut } from "@/lib/datasource-api";
import { ragApi, type RecallProbeOut } from "@/lib/rag-api";

/** 闸门判定归因 → 徽章(reason=passed 即过闸;其余为未过闸的归因;
    top1_below 为判定函数预留档,现判定不产出,StatusBadge 原文兜底) */
export const VERDICT_META: StatusMeta = {
  passed: { label: "过闸", variant: "success" },
  no_hits: { label: "零命中·不投票", variant: "secondary" },
  min_count: { label: "未过闸·命中数不足", variant: "warning" },
  top1_below: { label: "未过闸·首位分低于阈值", variant: "warning" },
  mean_below: { label: "未过闸·分值低于阈值", variant: "warning" },
};

/** 零知识旁路标注(零命中源于「无知识」而非「低相关」,闸门自动旁路) */
export const GATE_SKIP_META: StatusMeta = {
  no_knowledge: { label: "闸门未参与(无知识)", variant: "warning" },
};

/** 关系扩展开/关徽章(中性;零扩展与否由明细区如实展示) */
export const EXPANSION_META: StatusMeta = {
  on: { label: "已开启", variant: "secondary" },
  off: { label: "未开启", variant: "outline" },
};

/** 判定观测行文案(响应内可得的实际分值,不含阈值——契约不含即不展示) */
export function verdictObservation(
  top1: number,
  meanTopN: number,
  count: number,
  dropped: number,
): string {
  return `判定观测:top1 ${top1} · 均值 ${meanTopN} · 命中 ${count} 条 · 被裁 ${dropped} 条`;
}

/** 扩展后表集合文案(两段限定名串联;空集合如实为「(空)」) */
export function fusedAfterText(tables: { schema_name: string; table_name: string }[]): string {
  if (tables.length === 0) return "(空)";
  return tables.map((t) => `${t.schema_name}.${t.table_name}`).join("、");
}

/** 连接键文案(契约形态=dict 投影索引签名,运行时键为 left_col/right_col;
    空数组如实为「—」) */
export function joinKeysText(joinKeys: Record<string, string>[]): string {
  if (joinKeys.length === 0) return "—";
  return joinKeys.map((j) => `${j.left_col} = ${j.right_col}`).join(", ");
}

/** 命中项公共形态(各路 hits 比融合项多 source 字段,结构兼容) */
type ProbeHit = RecallProbeOut["fused"][number];

const HIT_COLUMNS_FOR = (scoreHeader: string): DataViewColumn<ProbeHit>[] => [
  {
    key: "table",
    header: "表",
    cardPriority: 1,
    render: (h) => (
      <span className="font-mono text-xs">
        {h.schema_name}.{h.table_name}
      </span>
    ),
  },
  {
    key: "score",
    header: scoreHeader,
    cardPriority: 2,
    render: (h) => <span className="font-mono text-xs">{h.score}</span>,
  },
];

/** 单路(向量/全文)命中区:标题 + 判定徽章/观测行 + 双形态命中列表 */
function RouteSection({ title, route }: { title: string; route: RecallProbeOut["vector"] }) {
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">{title}</h3>
        {route.verdict ? (
          <>
            <StatusBadge
              meta={VERDICT_META}
              value={route.verdict.reason}
              fallbackLabel="未知归因"
            />
            <span className="text-xs text-muted-foreground">
              {verdictObservation(
                route.verdict.top1,
                route.verdict.mean_top_n,
                route.verdict.count,
                route.dropped,
              )}
            </span>
          </>
        ) : (
          <span className="text-xs text-muted-foreground">闸门未参与判定</span>
        )}
      </div>
      <DataView
        rows={route.hits}
        rowKey={(h) => `${h.schema_name}.${h.table_name}`}
        isEmpty={route.hits.length === 0}
        emptyText="零命中(该路不参与判定)"
        columns={HIT_COLUMNS_FOR("原生分")}
      />
    </section>
  );
}

/** 关系扩展明细(定义列表:表少层级浅,列表组件过重——design 裁决):
    未开启/开启零扩展/开启有扩展三形态如实 */
function ExpansionDetail({ expansion }: { expansion: RecallProbeOut["expansion"] }) {
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">关系扩展</h3>
        <StatusBadge meta={EXPANSION_META} value={expansion.enabled ? "on" : "off"} />
      </div>
      {expansion.enabled ? (
        <dl className="space-y-1.5">
          {expansion.tables.length > 0 ? (
            expansion.tables.map((t) => (
              <div key={`${t.schema_name}.${t.table_name}`} className="flex flex-col gap-0.5">
                <dt className="text-xs text-muted-foreground">补入表</dt>
                <dd className="min-w-0 text-sm">
                  <span className="font-mono text-xs">
                    {t.schema_name}.{t.table_name}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {" "}
                    经由 {t.via.source_table} → {t.via.target_table}
                    (连接键 {joinKeysText(t.via.join_keys)};来源 {t.via.source})
                  </span>
                </dd>
              </div>
            ))
          ) : (
            <div className="text-xs text-muted-foreground">
              召回集无已声明关系的对端表,本轮零扩展
            </div>
          )}
          <div className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted-foreground">扩展后表集合</dt>
            <dd className="min-w-0 break-all text-sm">
              <span className="font-mono text-xs">{fusedAfterText(expansion.fused_after)}</span>
            </dd>
          </div>
        </dl>
      ) : (
        <p className="text-xs text-muted-foreground">扩展未开启,召回结果不含关系补入表</p>
      )}
    </section>
  );
}

/** 试跑结果(有响应才渲染):旁路标注 → 三路命中 → 扩展明细。
    导出供静态渲染守卫直接驱动三形态(零 jsdom,react-dom/server 出串断言) */
export function ProbeResult({ out }: { out: RecallProbeOut }) {
  return (
    <div className="space-y-4">
      {out.gate_skipped === "no_knowledge" && (
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge meta={GATE_SKIP_META} value={out.gate_skipped} fallbackLabel="闸门未参与" />
          <span className="text-xs text-muted-foreground">
            该数据源尚无知识条目,闸门整路旁路(非「全部通过」);命中明细如下
          </span>
        </div>
      )}
      <RouteSection title="向量路" route={out.vector} />
      <RouteSection title="全文路" route={out.fulltext} />
      <section className="space-y-2">
        <h3 className="text-sm font-medium">融合序(RRF 融合分,仅排序观测)</h3>
        <DataView
          rows={out.fused}
          rowKey={(h) => `${h.schema_name}.${h.table_name}`}
          isEmpty={out.fused.length === 0}
          emptyText="融合结果为空"
          columns={HIT_COLUMNS_FOR("融合分")}
        />
      </section>
      <ExpansionDetail expansion={out.expansion} />
    </div>
  );
}

/** 召回试跑面板(options 由页面注入:复用既有数据源下拉查询,不重复请求) */
export function RecallProbePanel({ options }: { options: DatasourceOptionOut[] }) {
  const [question, setQuestion] = useState("");
  const [ds, setDs] = useState("default");
  const probe = useMutation({ mutationFn: () => ragApi.probe(question.trim(), ds) });
  const out = probe.data;

  return (
    <section className="rounded-lg border p-4 space-y-3">
      <h2 className="text-sm font-medium">召回试跑</h2>
      <p className="text-xs text-muted-foreground">
        输入问句验证表说明可召回性与闸门判定;只读操作,不修改任何配置或数据。
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <Label htmlFor="rag-probe-question">问句</Label>
          <Textarea
            id="rag-probe-question"
            rows={1}
            maxLength={2000}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="如:查询库存数量"
            className="min-w-0"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="rag-probe-ds">数据源</Label>
          <Select value={ds} onValueChange={setDs}>
            <SelectTrigger id="rag-probe-ds" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {options.map((o) => (
                <SelectItem key={o.datasource_key} value={o.datasource_key}>
                  {o.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button disabled={probe.isPending || question.trim() === ""} onClick={() => probe.mutate()}>
          {probe.isPending ? "试跑中…" : "试跑"}
        </Button>
      </div>
      {/* 错误态收敛在面板内部(标准件 role=alert),不污染页面既有区块 */}
      {probe.isError && <ErrorAlert text={`试跑失败:${(probe.error as Error).message}`} />}
      {out ? (
        <ProbeResult out={out} />
      ) : (
        !probe.isPending &&
        !probe.isError && (
          <EmptyState>输入问句并选择数据源后点「试跑」,查看三路召回与闸门判定</EmptyState>
        )
      )}
    </section>
  );
}
