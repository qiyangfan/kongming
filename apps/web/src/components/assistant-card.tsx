/* 展示问答摘要、结果表、SQL、执行计划、反馈和类型化产物。 */

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import {
  ChevronRight,
  Database,
  Download,
  Info,
  Lightbulb,
  Loader2,
  Ruler,
  ShieldOff,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from "lucide-react";

import { ChartView } from "@/components/chart-view";
import {
  ChartArtifactCard,
  DocumentCard,
  ReportArtifactCard,
  SqlArtifactCard,
} from "@/components/artifact-blocks";
import type { ChartSpec } from "@/components/chart-shared";
import { ResultTable } from "@/components/result-table";
import { normalizeBinding } from "@/components/chart-shared";
import {
  CitationSourcesPanel,
  renderWithCitations,
  useCitationHighlight,
} from "@/components/citation-panel";
import type { ArtifactOut } from "@/lib/chat-api";
import { formatDateTime } from "@/lib/datetime";
import { humanizeErrorText, type SessionMessageOut } from "@/lib/api";
import { explainPlan, exportResult } from "@/lib/chat-api";
import { submitFeedback } from "@/lib/feedback-api";
import { useAuth } from "@/lib/auth-store";
import { hasPerm } from "@/lib/nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

/* 将可选结果字段收敛为展示状态；表头使用业务标签，悬停显示物理列名。 */
function normalize(msg: SessionMessageOut): {
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
    updated_at?: string | null;
  }[];
  sources: { citation_key: string; source_ref?: string; doc_name?: string }[];
} {
  const r = msg.result;
  // 候选优先(绑定随候选);存量档(仅单图)经 normalizeBinding 收为
  // 单元素绑定数组,两代档同构进渲染层;type=none → 空数组(不配图)
  const rawCands = r?.chart_candidates ?? [];
  const chartBindings: ChartSpec[] =
    rawCands.length > 0
      ? rawCands.map((c) => ({
          type: c.type,
          x: c.x ?? null,
          y: c.y ?? [],
          series: c.series ?? null,
        }))
      : [
          normalizeBinding({
            type: r?.chart?.type ?? "none",
            x: r?.chart?.x ?? null,
            y: r?.chart?.y ?? null,
          }),
        ].filter((b): b is ChartSpec => b !== null);
  return {
    columns: r?.columns ?? [],
    rows: r?.rows ?? [],
    insights: r?.insights ?? [],
    stats: r?.stats ?? {},
    chartBindings,
    artifacts: r?.artifacts ?? [],
    columnLabels: r?.column_labels ?? [],
    // 口径依据(指标命中);空=无命中或存量消息
    metricHits: (r?.metric_hits ?? []).map((m) => ({
      metric_id: m.metric_id,
      metric_name: m.metric_name,
      sql_template: m.sql_template,
      updated_at: m.updated_at,
    })),
    // 文档来源;空=SQL 路回答或存量消息
    sources: (r?.sources ?? []).map((x) => ({
      citation_key: x.citation_key,
      source_ref: x.source_ref,
      doc_name: x.doc_name,
    })),
  };
}

export function AssistantCard({ msg }: { msg: SessionMessageOut }) {
  // 融合消息:正文引用角标定位来源条目(与 DocumentCard 同机制;
  // hook 须在条件返回前调用)
  const { activeKey, highlight } = useCitationHighlight();
  if (msg.status === "REJECTED") {
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
  if (msg.status === "NO_DATA") {
    // 知识未覆盖如实说明——中性说明卡(非失败),文案在 summary;
    // 反馈保留:数据源不对/答案没帮上忙恰是高价值信号
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
  if (msg.status === "FAILED") {
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
        {/* 触顶错误仍展示已生成的部分结果，并保留 EXPLAIN 入口。 */}
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
  const sqlArt = result.artifacts.find((a) => a.type === "sql");
  const chartArt = result.artifacts.find((a) => a.type === "chart");
  const docArt = result.artifacts.find((a) => a.type === "document");
  return (
    <div className="space-y-3">
      {docArt ? (
        <DocumentCard content={docArt.content} sources={result.sources} />
      ) : msg.summary && result.sources.length > 0 ? (
        /* 融合消息可同时显示正文引用、来源列表和 SQL 结果。 */
        <div className="space-y-2 px-1">
          <p className="text-md leading-relaxed text-foreground">
            {renderWithCitations(msg.summary, result.sources, highlight)}
          </p>
          <CitationSourcesPanel sources={result.sources} activeKey={activeKey} />
        </div>
      ) : (
        msg.summary && <p className="px-1 text-md leading-relaxed text-foreground">{msg.summary}</p>
      )}

      {chartArt && result.columns.length > 0 && result.chartBindings.length > 0 ? (
        <ChartArtifactCard
          candidates={result.chartBindings}
          data={{ columns: result.columns, rows: result.rows }}
          labels={result.columnLabels}
        />
      ) : (
        result.chartBindings.length > 0 &&
        result.columns.length > 0 && (
          <div className="rounded-lg border border-border bg-card px-2 py-1">
            <ChartView
              candidates={result.chartBindings}
              data={{ columns: result.columns, rows: result.rows }}
              labels={result.columnLabels}
            />
          </div>
        )
      )}

      {result.columns.length > 0 && (
        <ResultTable columns={result.columns} rows={result.rows} labels={result.columnLabels} />
      )}

      {result.columns.length > 0 && (
        <ExportButtons columns={result.columns} rows={result.rows} labels={result.columnLabels} />
      )}

      {sqlArt ? (
        <SqlArtifactCard messageId={msg.id} sql={sqlArt.content} />
      ) : (
        msg.sql && (
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
        )
      )}

      {msg.sql && msg.id && <ExplainCollapsible messageId={msg.id} />}
      {/* 未落库的消息没有 message_id，因此隐藏执行计划查询入口。 */}

      {/* 口径依据面板:有语义层命中才渲染;指标名跳 /admin/semantic 定位定义 */}
      {result.metricHits.length > 0 && <MetricProvenancePanel hits={result.metricHits} />}

      {(result.insights?.length ?? 0) > 0 && (
        <div className="space-y-1">
          {result.insights.map((ins, i) => (
            <p key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
              <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-ring" aria-hidden />
              {ins.text ?? Object.values(ins)[0]}
            </p>
          ))}
        </div>
      )}

      {result.stats && Object.keys(result.stats).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(result.stats).map(([k, v]) => (
            <Badge key={k} variant="secondary">
              {k} {String(v)}
            </Badge>
          ))}
        </div>
      )}

      <FeedbackActions msg={msg} />
    </div>
  );
}

/* 反馈行：赞立即提交，踩可编辑说明和纠错 SQL；无权限或无消息 ID 时不显示。 */
function FeedbackActions({ msg }: { msg: SessionMessageOut }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!hasPerm(user, "chat:feedback:submit") || !msg.id) return null;
  const mid = msg.id;

  async function submit(payload: {
    rating: "UP" | "DOWN";
    comment?: string | null;
    corrected_sql?: string | null;
  }): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    setError(null);
    try {
      const data = await submitFeedback(mid, payload);
      // 实时消息经 setQueriesData 更新所有含该消息的缓存,激活态即时回显
      qc.setQueriesData({ queryKey: ["messages"] }, (old: unknown) =>
        Array.isArray(old)
          ? old.map((m) =>
              m && typeof m === "object" && (m as { id?: string }).id === mid
                ? {
                    ...m,
                    rating: data.rating,
                    corrected_sql: data.corrected_sql,
                    feedback_comment: data.comment,
                  }
                : m,
            )
          : old,
      );
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "反馈未成,请稍后再试。");
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="有用"
        aria-pressed={msg.rating === "UP"}
        title="有用"
        disabled={busy}
        onClick={() => void submit({ rating: "UP" })}
        className={msg.rating === "UP" ? "text-primary" : "text-muted-foreground"}
      >
        <ThumbsUp aria-hidden className={msg.rating === "UP" ? "fill-current" : undefined} />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label="不准确"
        aria-pressed={msg.rating === "DOWN"}
        title="不准确"
        disabled={busy}
        onClick={() => setDialogOpen(true)}
        className={msg.rating === "DOWN" ? "text-primary" : "text-muted-foreground"}
      >
        <ThumbsDown aria-hidden className={msg.rating === "DOWN" ? "fill-current" : undefined} />
      </Button>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <FeedbackDialog
        msg={msg}
        open={dialogOpen}
        busy={busy}
        onOpenChange={(o) => {
          if (busy) return; // 提交中不可关闭
          setDialogOpen(o);
        }}
        onSubmit={async (comment, correctedSql) => {
          const ok = await submit({
            rating: "DOWN",
            comment,
            corrected_sql: correctedSql,
          });
          if (ok) setDialogOpen(false);
        }}
      />
    </div>
  );
}

/* 点踩弹窗预填已有说明和纠错 SQL；未修改字段保持原值，清空字段则提交删除。 */
function FeedbackDialog({
  msg,
  open,
  busy,
  onOpenChange,
  onSubmit,
}: {
  msg: SessionMessageOut;
  open: boolean;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (comment: string | null, correctedSql: string) => Promise<void>;
}) {
  const [comment, setComment] = useState("");
  const [sql, setSql] = useState("");
  const [commentTouched, setCommentTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setComment(msg.feedback_comment ?? "");
      setSql(msg.corrected_sql ?? msg.sql ?? "");
      setCommentTouched(false);
    }
  }, [open, msg.feedback_comment, msg.corrected_sql, msg.sql]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>此答不准确?</DialogTitle>
          <DialogDescription>
            留下指正,军师会记住——纠错 SQL 将作为同类问题的参考。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            // comment 未触碰传 null=保持现值;触碰后按显式值(空串=清空)
            void onSubmit(commentTouched ? comment : null, sql);
          }}
        >
          <div className="space-y-1.5">
            <label htmlFor="fb-comment" className="text-sm text-muted-foreground">
              留言(可留空)
            </label>
            <Textarea
              id="fb-comment"
              value={comment}
              onChange={(e) => {
                setComment(e.target.value);
                setCommentTouched(true);
              }}
              placeholder="哪里不对?"
              rows={2}
              maxLength={500}
            />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="fb-sql" className="text-sm text-muted-foreground">
              正确的 SQL(清空即撤销纠错)
            </label>
            <Textarea
              id="fb-sql"
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              rows={4}
              maxLength={4000}
              className="font-mono text-xs"
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              提交指正
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/* 导出当前展示的列和行；表头优先使用业务标签，失败时在卡片内提示。 */
function ExportButtons({
  columns,
  rows,
  labels,
}: {
  columns: string[];
  rows: unknown[][];
  labels?: string[];
}) {
  const [busy, setBusy] = useState<"csv" | "xlsx" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(format: "csv" | "xlsx"): Promise<void> {
    if (busy) return;
    setBusy(format);
    setError(null);
    try {
      const shown = columns.map((c, i) => labels?.[i] || c);
      await exportResult(format, shown, rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出未成,请稍后再试。");
    }
    setBusy(null);
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-muted-foreground">导出</span>
      <Button variant="ghost" size="xs" onClick={() => void run("csv")} disabled={busy !== null}>
        {busy === "csv" ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <Download aria-hidden />
        )}
        CSV
      </Button>
      <Button variant="ghost" size="xs" onClick={() => void run("xlsx")} disabled={busy !== null}>
        {busy === "xlsx" ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <Download aria-hidden />
        )}
        Excel
      </Button>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/* 展开时按需加载执行计划；异常结构以 JSON 展示，客户端只传 message_id。 */
function ExplainCollapsible({ messageId }: { messageId: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    if (text !== null || error !== null || loading) return; // 成功即缓存;失败见重试钮
    setLoading(true);
    try {
      const out = await explainPlan(messageId);
      if (out.success) {
        setText(renderPlan(out.plan));
      } else {
        setError(out.error ?? "执行计划获取失败,请稍后再试。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "执行计划获取失败,请稍后再试。");
    }
    setLoading(false);
  }

  return (
    <Collapsible
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) void load();
      }}
    >
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden
        />
        <Database className="size-3.5" aria-hidden />
        执行计划
      </CollapsibleTrigger>
      <CollapsibleContent>
        {loading ? (
          <p className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" aria-hidden />
            计划生成中…
          </p>
        ) : error ? (
          <p role="alert" className="mt-1.5 flex items-center gap-2 text-xs text-destructive">
            {error}
            <Button
              variant="ghost"
              size="xs"
              onClick={() => {
                setError(null);
                void load();
              }}
            >
              重试
            </Button>
          </p>
        ) : (
          <pre className="mt-1.5 overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed text-foreground">
            {text}
          </pre>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

/* 格式化 PostgreSQL EXPLAIN 的计划树、节点类型、成本、行数和关联表。 */
function renderPlan(plan: unknown): string {
  if (Array.isArray(plan) && plan.length > 0) {
    const first: unknown = plan[0];
    if (first && typeof first === "object") {
      const root = (first as Record<string, unknown>)["Plan"];
      if (root && typeof root === "object") {
        return formatPlanNode(root as Record<string, unknown>, 0);
      }
    }
  }
  return JSON.stringify(plan, null, 2);
}

function formatPlanNode(node: Record<string, unknown>, depth: number): string {
  const indent = "  ".repeat(depth);
  const parts = [String(node["Node Type"] ?? "节点")];
  const cost = Number(node["Total Cost"]);
  if (Number.isFinite(cost)) parts.push(`cost=${cost.toFixed(2)}`); // 非数值不渲染 NaN
  const rows = node["Actual Rows"] ?? node["Plan Rows"];
  if (rows !== undefined) parts.push(`rows=${String(rows)}`);
  if (node["Relation Name"]) parts.push(`on ${String(node["Relation Name"])}`);
  const lines = [indent + parts.join("  ")];
  const children = node["Plans"];
  if (Array.isArray(children)) {
    for (const c of children) {
      if (c && typeof c === "object") {
        lines.push(formatPlanNode(c as Record<string, unknown>, depth + 1));
      }
    }
  }
  return lines.join("\n");
}

/* 展示指标名称、口径表达式和修订时间；管理链接由路由权限守卫处理。 */
function MetricProvenancePanel({
  hits,
}: {
  hits: {
    metric_id: string;
    metric_name: string;
    sql_template: string;
    updated_at?: string | null;
  }[];
}) {
  const navigate = useNavigate();
  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden
        />
        <Ruler className="size-3.5" aria-hidden />
        口径依据({hits.length})
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="mt-1.5 space-y-2 rounded-md border border-border bg-muted/40 p-3">
          {hits.map((h) => (
            <li key={h.metric_id || h.metric_name} className="space-y-0.5">
              <Button
                variant="link"
                size="xs"
                className="h-auto p-0 text-xs font-medium"
                onClick={() => navigate("/admin/semantic")}
              >
                {h.metric_name}
              </Button>
              <p className="font-mono text-xs text-muted-foreground">{h.sql_template}</p>
              {h.updated_at && (
                <p className="text-2xs text-muted-foreground">
                  修订于 {formatDateTime(h.updated_at)}
                </p>
              )}
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}
