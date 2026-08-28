/* assistant 结果卡片(web-nav spec「对话区渲染」+ 010 导出/执行计划 + 014 赞踩反馈):
   摘要/表格/SQL 折叠/执行计划懒加载/洞察/统计/反馈操作行;SUCCESS/REJECTED/FAILED 三态视觉区分。 */

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  Database,
  Download,
  Lightbulb,
  Loader2,
  ShieldOff,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
} from "lucide-react";

import { ChartView } from "@/components/chart-view";
import {
  ChartArtifactCard,
  DocumentCard,
  SqlArtifactCard,
} from "@/components/artifact-blocks";
import type { ArtifactOut } from "@/lib/chat-api";
import type { SessionMessageOut } from "@/lib/api";
import { explainPlan, exportResult } from "@/lib/chat-api";
import { submitFeedback } from "@/lib/feedback-api";
import { useAuth } from "@/lib/auth-store";
import { hasPerm } from "@/lib/nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

/* 生成类型的 result 字段皆可选:本地归一化收窄 */
function ResultTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            {columns.map((c) => (
              <th
                key={c}
                className="px-3 py-2 text-left font-medium whitespace-nowrap text-muted-foreground"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={Math.max(columns.length, 1)}
                className="px-3 py-6 text-center text-muted-foreground"
              >
                查无数据,秋叶落,结果空。
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={i} className="border-t border-border">
                {row.map((cell, j) => (
                  <td
                    key={j}
                    className="max-w-80 truncate px-3 py-1.5 text-foreground"
                    title={cell === null || cell === undefined ? undefined : String(cell) || undefined}
                  >
                    {cell === null || cell === undefined ? "—" : String(cell)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function normalize(msg: SessionMessageOut): {
  columns: string[];
  rows: unknown[][];
  insights: { [key: string]: string }[];
  stats: Record<string, unknown>;
  chart: { type: string; x: string | null; y: string | null };
  artifacts: ArtifactOut[];
} {
  const r = msg.result;
  return {
    columns: r?.columns ?? [],
    rows: r?.rows ?? [],
    insights: r?.insights ?? [],
    stats: r?.stats ?? {},
    chart: {
      type: r?.chart?.type ?? "none",
      x: r?.chart?.x ?? null,
      y: r?.chart?.y ?? null,
    },
    artifacts: r?.artifacts ?? [],
  };
}

export function AssistantCard({ msg }: { msg: SessionMessageOut }) {
  if (msg.status === "REJECTED") {
    return (
      <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
        <ShieldOff className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
        <div>
          <p className="font-medium text-foreground">
            {msg.error ?? "该查询已被安全策略拦下"}
          </p>
          {msg.summary && <p className="mt-1 text-muted-foreground">{msg.summary}</p>}
        </div>
      </div>
    );
  }
  if (msg.status === "FAILED") {
    const partial = normalize(msg);
    return (
      <div className="space-y-3">
        <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
          <p className="text-foreground">{msg.error ?? "查询失败,请换个问法试试"}</p>
        </div>
        {/* R2-F2:触顶等形态携带的部分结果不丢弃——错误优先呈现,
            已产出结果集作参考附带(EXPLAIN 入口同步可达) */}
        {partial.columns.length > 0 && (
          <>
            <ResultTable columns={partial.columns} rows={partial.rows} />
            {msg.sql && msg.id && <ExplainCollapsible messageId={msg.id} />}
          </>
        )}
      </div>
    );
  }

  const result = normalize(msg);
  // 026:artifacts 非空走类型化产物卡;缺档(026 前存量/persist=false 容错)
  // 全段回落既有形态(spec「无 artifacts 的历史消息按既有形态渲染」)
  const sqlArt = result.artifacts.find((a) => a.type === "sql");
  const chartArt = result.artifacts.find((a) => a.type === "chart");
  const docArt = result.artifacts.find((a) => a.type === "document");
  return (
    <div className="space-y-3">
      {docArt ? (
        <DocumentCard content={docArt.content} />
      ) : (
        msg.summary && (
          <p className="px-1 text-[15px] leading-relaxed text-foreground">{msg.summary}</p>
        )
      )}

      {chartArt && result.columns.length > 0 ? (
        <ChartArtifactCard
          chart={result.chart}
          data={{ columns: result.columns, rows: result.rows }}
        />
      ) : (
        result.chart.type !== "none" &&
        result.columns.length > 0 && (
          <div className="rounded-lg border border-border bg-card px-2 py-1">
            <ChartView chart={result.chart} data={{ columns: result.columns, rows: result.rows }} />
          </div>
        )
      )}

      {result.columns.length > 0 && (
        <ResultTable columns={result.columns} rows={result.rows} />
      )}

      {result.columns.length > 0 && (
        <ExportButtons columns={result.columns} rows={result.rows} />
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
      {/* msg.id 守卫(017 乐观快照容错分支 id 可能为空串):
          无落库 id 则无执行计划可查,渲染会 422 白点 */}

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
            <Badge key={k} variant="secondary" className="font-normal">
              {k} {String(v)}
            </Badge>
          ))}
        </div>
      )}

      <FeedbackActions msg={msg} />
    </div>
  );
}

/* 反馈操作行(014 feedback spec「前端交互」):赞=立即提交;踩=弹窗收集 comment
   与 correctedSql;已反馈回显激活态(历史=服务端字段,实时=setQueriesData 本地
   状态);无 chat:feedback:submit 权限或无 messageId 不渲染入口(spec)。
   仅 SUCCESS 卡片挂载:REJECTED=系统拦截、FAILED=执行失败,均非「错答」语义 */
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
      // 实时消息本地状态(spec):前缀 setQueriesData 更新含该消息的全部 messages
      // 缓存,激活态即时回显零重拉
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
          if (busy) return; // 提交中不可关(与删除对话框同范式)
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

/* 点踩弹窗(014):预填已提交 comment/correctedSql 或当前 SQL,可改可清空
   (清空提交即撤销纠错/留言);未触碰的字段传 null=保持现值(R1-S3:
   此前空串恒提交,重开弹窗直接提交会静默清空既有留言);提交中禁用防重复 */
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

/* 结果导出(010):所见即所导(传展示中的 columns+rows);失败行内提示不炸卡片 */
function ExportButtons({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  const [busy, setBusy] = useState<"csv" | "xlsx" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(format: "csv" | "xlsx"): Promise<void> {
    if (busy) return;
    setBusy(format);
    setError(null);
    try {
      await exportResult(format, columns, rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出未成,请稍后再试。");
    }
    setBusy(null);
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-muted-foreground">导出</span>
      <Button
        variant="ghost"
        size="xs"
        onClick={() => void run("csv")}
        disabled={busy !== null}
      >
        {busy === "csv" ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <Download aria-hidden />
        )}
        CSV
      </Button>
      <Button
        variant="ghost"
        size="xs"
        onClick={() => void run("xlsx")}
        disabled={busy !== null}
      >
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

/* 执行计划(010):展开时懒加载一次;计划树递归缩进为等宽文本(上游形态移植),
   非预期形态降级 JSON 展示;message_id 引用,客户端不提交 SQL */
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
            {/* R1-S1:失败态给出重试出口(此前 error 非 null 即永久锁死) */}
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

/* EXPLAIN (FORMAT JSON) 顶层为 [{Plan: {...}}];逐层缩进渲染
   Node Type / cost=Total Cost / rows=Plan Rows / on Relation Name */
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
  if (Number.isFinite(cost)) parts.push(`cost=${cost.toFixed(2)}`); // R1-O4:非数值不渲染 NaN
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
