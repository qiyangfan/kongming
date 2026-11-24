/* 展示问答摘要、结果表、SQL、执行计划、反馈和类型化产物。 */

import { Info, Lightbulb, ShieldOff, TriangleAlert } from "lucide-react";

import { ChartView } from "@/components/chart-view";
import {
  ChartArtifactCard,
  DocumentCard,
  ReportArtifactCard,
  SqlArtifactCard,
} from "@/components/artifact-blocks";
import type { ChartSpec } from "@/lib/chart-shared";
import { ResultTable } from "@/components/result-table";
import { hasChartToRender, normalizeBinding } from "@/lib/chart-shared";
import {
  CitationSourcesPanel,
  renderWithCitations,
  useCitationHighlight,
} from "@/components/citation-panel";
import type { ArtifactOut } from "@/lib/chat-api";
import { humanizeErrorText, type SessionMessageOut } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { FeedbackActions } from "@/components/assistant-feedback";
import { ExportButtons } from "@/components/assistant-export";
import { ExplainCollapsible } from "@/components/assistant-explain";
import { MetricProvenancePanel } from "@/components/metric-provenance-panel";
import { ChevronRight, Database } from "lucide-react";

type ChatResult = Exclude<SessionMessageOut["result"], null | undefined>;
type ChartCandidate = NonNullable<ChatResult["chart_candidates"]>[number];
type MetricHitOut = NonNullable<ChatResult["metric_hits"]>[number];
type SourceOut = NonNullable<ChatResult["sources"]>[number];

type NormalizedResult = {
  columns: string[];
  rows: unknown[][];
  insights: { [key: string]: string }[];
  stats: Record<string, unknown>;
  chartBindings: ChartSpec[];
  artifacts: ArtifactOut[];
  columnLabels: string[];
  metricHits: {
    metric_id: string;
    metric_name: string;
    sql_template: string;
    updated_at?: string | null | undefined;
  }[];
  sources: { citation_key: string; source_ref?: string; doc_name?: string }[];
};

/* 候选优先(绑定随候选);存量档(仅单图)经 normalizeBinding 收为单元素绑定数组,两代档同构进渲染层;type=none → 空数组(不配图) */
function toChartBindings(r: SessionMessageOut["result"]): ChartSpec[] {
  const rawCands: ChartCandidate[] = r?.chart_candidates ?? [];
  if (rawCands.length > 0) {
    return rawCands.map((c) => ({
      type: c.type,
      x: c.x ?? null,
      y: c.y ?? [],
      series: c.series ?? null,
    }));
  }
  return [
    normalizeBinding({
      type: r?.chart?.type ?? "none",
      x: r?.chart?.x ?? null,
      y: r?.chart?.y ?? null,
    }),
  ].filter((b): b is ChartSpec => b !== null);
}

function toMetricHits(r: SessionMessageOut["result"]): NormalizedResult["metricHits"] {
  return (r?.metric_hits ?? []).map((m: MetricHitOut) => ({
    metric_id: m.metric_id,
    metric_name: m.metric_name,
    sql_template: m.sql_template,
    updated_at: m.updated_at,
  }));
}

function toSources(r: SessionMessageOut["result"]): NormalizedResult["sources"] {
  return (r?.sources ?? []).map((x: SourceOut) => ({
    citation_key: x.citation_key,
    source_ref: x.source_ref,
    doc_name: x.doc_name,
  }));
}

/* 将可选结果字段收敛为展示状态；表头使用业务标签，悬停显示物理列名。 */
function normalize(msg: SessionMessageOut): NormalizedResult {
  const r = msg.result;
  return {
    columns: r?.columns ?? [],
    rows: r?.rows ?? [],
    insights: r?.insights ?? [],
    stats: r?.stats ?? {},
    chartBindings: toChartBindings(r),
    artifacts: r?.artifacts ?? [],
    columnLabels: r?.column_labels ?? [],
    // 口径依据(指标命中);空=无命中或存量消息
    metricHits: toMetricHits(r),
    // 文档来源;空=SQL 路回答或存量消息
    sources: toSources(r),
  };
}

/* 安全护栏拦截卡；summary 补充拦截说明。 */
function RejectedCard({ msg }: { msg: SessionMessageOut }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
      <ShieldOff className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
      <div>
        <p className="font-medium text-foreground">
          {msg.error ? humanizeErrorText(msg.error) : "此问被安全护栏拦下"}
        </p>
        {msg.summary && <p className="mt-1 text-muted-foreground">{msg.summary}</p>}
      </div>
    </div>
  );
}

/* 知识未覆盖如实说明——中性说明卡(非失败),文案在 summary;反馈保留:数据源不对/答案没帮上忙恰是高价值信号 */
function NoDataCard({ msg }: { msg: SessionMessageOut }) {
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
        <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
        <p className="text-foreground">
          {msg.summary ?? "当前数据源未找到回答该问题所需的数据,可切换数据源或调整问法后重试"}
        </p>
      </div>
      <FeedbackActions msg={msg} />
    </div>
  );
}

/* 已取消说明卡:用户主动放弃的一轮只留简洁说明,不提供反馈/纠错/再执行/导出/执行计划入口。 */
function CancelledCard() {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
      <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div>
        <p className="text-foreground">本轮已取消</p>
        <p className="mt-0.5 text-xs text-muted-foreground">已开始执行的操作不会被撤回。</p>
      </div>
    </div>
  );
}

/* 查询失败卡：折叠展示原始技术错误；触顶错误仍展示已生成的部分结果，并保留 EXPLAIN 入口。 */
function FailedCard({ msg }: { msg: SessionMessageOut }) {
  const partial = normalize(msg);
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        <div className="min-w-0">
          <p className="text-foreground">
            {msg.error ? humanizeErrorText(msg.error) : "查询执行失败,请调整问法后重试"}
          </p>
          {/* 原始技术错误默认折叠，方便排障且不干扰主要结果。 */}
          {msg.error_detail && (
            <Collapsible>
              <CollapsibleTrigger className="mt-1 text-2xs text-muted-foreground underline-offset-2 hover:underline">
                技术详情
              </CollapsibleTrigger>
              <CollapsibleContent>
                <pre className="mt-1.5 max-h-40 overflow-auto rounded-md bg-background p-2 font-mono text-3xs break-all whitespace-pre-wrap text-muted-foreground">
                  {msg.error_detail}
                </pre>
              </CollapsibleContent>
            </Collapsible>
          )}
        </div>
      </div>
      {partial.columns.length > 0 && (
        <>
          <ResultTable
            columns={partial.columns}
            rows={partial.rows}
            labels={partial.columnLabels}
          />
          {msg.sql && msg.id && <ExplainCollapsible messageId={msg.id} />}
        </>
      )}
    </div>
  );
}

type SummaryProps = {
  msg: SessionMessageOut;
  result: NormalizedResult;
  docArt: ArtifactOut | undefined;
  highlight: (key: string) => void;
  activeKey: string | null;
};

/* 正文区：文档产物优先；融合消息同时显示正文引用角标与来源列表；否则只渲染摘要。 */
function CardSummary({ msg, result, docArt, highlight, activeKey }: SummaryProps) {
  if (docArt) {
    return <DocumentCard content={docArt.content} sources={result.sources} />;
  }
  if (msg.summary && result.sources.length > 0) {
    return (
      <div className="space-y-2 px-1">
        <p className="text-md leading-relaxed text-foreground">
          {renderWithCitations(msg.summary, result.sources, highlight)}
        </p>
        <CitationSourcesPanel sources={result.sources} activeKey={activeKey} />
      </div>
    );
  }
  if (!msg.summary) return null;
  return <p className="px-1 text-md leading-relaxed text-foreground">{msg.summary}</p>;
}

/* 图表面板：有 chart 产物时带调参设置，否则渲染只读图表；无有效数据时不渲染。 */
function ChartSlot({
  result,
  chartArt,
}: {
  result: NormalizedResult;
  chartArt: ArtifactOut | undefined;
}) {
  if (!hasChartToRender(result.columns, result.chartBindings)) return null;
  const data = { columns: result.columns, rows: result.rows };
  if (chartArt) {
    return (
      <ChartArtifactCard
        candidates={result.chartBindings}
        data={data}
        labels={result.columnLabels}
      />
    );
  }
  return (
    <div className="rounded-lg border border-border bg-card px-2 py-1">
      <ChartView candidates={result.chartBindings} data={data} labels={result.columnLabels} />
    </div>
  );
}

/* SQL 面板：有 sql 产物时用可编辑重跑卡，否则折叠展示原始 SQL。 */
function SqlSlot({ msg, sqlArt }: { msg: SessionMessageOut; sqlArt: ArtifactOut | undefined }) {
  if (sqlArt) return <SqlArtifactCard messageId={msg.id} sql={sqlArt.content} />;
  if (!msg.sql) return null;
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden
        />
        <Database className="size-3.5" aria-hidden />
        查看 SQL
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-1.5 overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed text-foreground">
          {msg.sql}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}

/* 洞察条目列表；空列表不渲染。 */
function InsightsList({ insights }: { insights: NormalizedResult["insights"] }) {
  if (insights.length === 0) return null;
  return (
    <div className="space-y-1">
      {insights.map((ins, i) => (
        <p key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
          <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-ring" aria-hidden />
          {ins.text ?? Object.values(ins)[0]}
        </p>
      ))}
    </div>
  );
}

/* 结果统计徽标；空对象不渲染。 */
function StatsBadges({ stats }: { stats: Record<string, unknown> }) {
  if (Object.keys(stats).length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(stats).map(([k, v]) => (
        <Badge key={k} variant="secondary">
          {k} {String(v)}
        </Badge>
      ))}
    </div>
  );
}

/* 查询成功卡：正文、图表、结果表、导出、SQL、执行计划、口径依据、洞察与统计依次排列。 */
function ResultCard({
  msg,
  result,
  activeKey,
  highlight,
}: {
  msg: SessionMessageOut;
  result: NormalizedResult;
  activeKey: string | null;
  highlight: (key: string) => void;
}) {
  const findArt = (type: string): ArtifactOut | undefined =>
    result.artifacts.find((a) => a.type === type);
  const hasColumns = result.columns.length > 0;
  return (
    <div className="space-y-3">
      <CardSummary
        msg={msg}
        result={result}
        docArt={findArt("document")}
        highlight={highlight}
        activeKey={activeKey}
      />

      <ChartSlot result={result} chartArt={findArt("chart")} />

      {hasColumns && (
        <ResultTable columns={result.columns} rows={result.rows} labels={result.columnLabels} />
      )}

      {hasColumns && (
        <ExportButtons columns={result.columns} rows={result.rows} labels={result.columnLabels} />
      )}

      <SqlSlot msg={msg} sqlArt={findArt("sql")} />

      {msg.sql && msg.id && <ExplainCollapsible messageId={msg.id} />}
      {/* 未落库的消息没有 message_id，因此隐藏执行计划查询入口。 */}

      {/* 口径依据面板:有语义层命中才渲染;指标名跳 /admin/semantic 定位定义 */}
      {result.metricHits.length > 0 && <MetricProvenancePanel hits={result.metricHits} />}

      <InsightsList insights={result.insights} />

      <StatsBadges stats={result.stats} />

      <FeedbackActions msg={msg} />
    </div>
  );
}

/* 状态分派：拦截/无数据/失败/已取消各自渲染专属卡片；其余按产物渲染结果或报告卡。 */
export function AssistantCard({ msg }: { msg: SessionMessageOut }) {
  // 融合消息:正文引用角标定位来源条目(与 DocumentCard 同机制;
  // hook 须在条件返回前调用)
  const { activeKey, highlight } = useCitationHighlight();
  if (msg.status === "REJECTED") return <RejectedCard msg={msg} />;
  if (msg.status === "NO_DATA") return <NoDataCard msg={msg} />;
  if (msg.status === "FAILED") return <FailedCard msg={msg} />;
  if (msg.status === "CANCELLED") return <CancelledCard />;

  const result = normalize(msg);
  // 有产物时按类型渲染；旧消息和 persist=false 的结果沿用原展示方式。
  // 报告轮次使用报告卡，不再叠加普通问答卡片。
  const reportArt = result.artifacts.find((a) => a.type === "report");
  if (reportArt) {
    return (
      <div className="max-w-full">
        <ReportArtifactCard content={reportArt.content} metadata={reportArt.metadata} />
      </div>
    );
  }
  return <ResultCard msg={msg} result={result} activeKey={activeKey} highlight={highlight} />;
}
