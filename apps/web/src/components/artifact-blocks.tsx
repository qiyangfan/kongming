/* 按类型渲染 SQL、图表、报告和文档产物；SQL 重跑结果追加为新消息，不覆盖原卡。 */

import { useId, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import {
  Check,
  ChevronRight,
  Copy,
  Database,
  FileText,
  Loader2,
  Play,
  SlidersHorizontal,
} from "lucide-react";

import { ChartView, type ChartDatum, type ChartTune } from "@/components/chart-view";
import type { ChartSpec } from "@/components/chart-shared";
import { MarkdownText } from "@/components/markdown-text";
import { rerunArtifact } from "@/lib/chat-api";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  CitationSourcesPanel,
  renderWithCitations,
  useCitationHighlight,
  type CitationSource,
} from "@/components/citation-panel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/* SQL 重跑前由服务端复检；成功结果作为新消息追加。无 messageId 时隐藏入口，避免发送无效请求。 */
export function SqlArtifactCard({ messageId, sql }: { messageId: string; sql: string }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState(sql);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(): Promise<void> {
    if (busy || !messageId) return;
    const sqlText = draft.trim();
    if (!sqlText) {
      setError("SQL 为空,无法执行。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await rerunArtifact(messageId, sqlText);
      // 新消息由 refetch 入列(会话尾部);按 ["messages"] 前缀失效覆盖各会话查询
      void qc.invalidateQueries({ queryKey: ["messages"] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "再执行未成,请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Collapsible>
      <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronRight
          className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden
        />
        <Database className="size-3.5" aria-hidden />
        SQL(可编辑再执行)
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1.5 space-y-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.min(Math.max(sql.split("\n").length, 3), 12)}
            maxLength={4000}
            aria-label="编辑 SQL"
            className="font-mono text-xs leading-relaxed"
          />
          <div className="flex items-center gap-2">
            {messageId && (
              <Button variant="outline" size="xs" onClick={() => void run()} disabled={busy}>
                {busy ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
                重新执行
              </Button>
            )}
            {draft !== sql && (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => {
                  setDraft(sql);
                  setError(null);
                }}
                disabled={busy}
              >
                还原
              </Button>
            )}
            {error && (
              <p role="alert" className="text-xs text-destructive">
                {error}
              </p>
            )}
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/* 图表调参只更新本地状态，由 ChartView 即时重绘。 */
export function ChartArtifactCard({
  candidates,
  data,
  labels,
}: {
  candidates: ChartSpec[];
  data: ChartDatum;
  labels?: string[];
}) {
  const [tune, setTune] = useState<ChartTune>({ title: "", xName: "", yName: "" });
  // 调参占位与图表轴共用列标签;列不在清单时回退物理列名
  const colLabel = (col?: string | null): string | null => {
    if (!col) return null;
    const i = data.columns.indexOf(col);
    return i >= 0 ? labels?.[i] || col : col;
  };
  // 同页多张 chart 卡,id 硬编码会重复(点 Label 聚焦到首卡);
  // useId 后缀保证每卡唯一
  const uid = useId();

  return (
    <div className="rounded-lg border border-border bg-card px-2 py-1">
      <ChartView candidates={candidates} data={data} tune={tune} labels={labels} />
      <Collapsible>
        <CollapsibleTrigger className="group flex items-center gap-1 px-2 pb-1 text-xs text-muted-foreground hover:text-foreground">
          <ChevronRight
            className="size-3.5 transition-transform group-data-[state=open]:rotate-90"
            aria-hidden
          />
          <SlidersHorizontal className="size-3.5" aria-hidden />
          设置
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="grid grid-cols-1 gap-2 px-2 pt-1 pb-2 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor={`chart-tune-title-${uid}`} className="text-xs">
                标题
              </Label>
              <Input
                id={`chart-tune-title-${uid}`}
                value={tune.title}
                onChange={(e) => setTune((t) => ({ ...t, title: e.target.value }))}
                maxLength={60}
                placeholder="留空不显示"
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`chart-tune-x-${uid}`} className="text-xs">
                X 轴名
              </Label>
              <Input
                id={`chart-tune-x-${uid}`}
                value={tune.xName}
                onChange={(e) => setTune((t) => ({ ...t, xName: e.target.value }))}
                maxLength={30}
                placeholder={colLabel(candidates[0]?.x) ?? "留空不显示"}
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`chart-tune-y-${uid}`} className="text-xs">
                Y 轴名
              </Label>
              <Input
                id={`chart-tune-y-${uid}`}
                value={tune.yName}
                onChange={(e) => setTune((t) => ({ ...t, yName: e.target.value }))}
                maxLength={30}
                placeholder={colLabel(candidates[0]?.y[0]) ?? "留空不显示"}
                className="h-8 text-xs"
              />
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

/* 报告卡显示标题和终态摘要，点击后打开报告详情。 */
export function ReportArtifactCard({
  content,
  metadata,
}: {
  content: string;
  metadata: Record<string, unknown>;
}) {
  const reportId = String(metadata.report_id ?? "");
  if (!reportId) return null;
  return (
    <Link
      to={`/reports/${reportId}`}
      className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground transition-colors hover:bg-accent"
    >
      <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1 truncate">{content}</span>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
    </Link>
  );
}

/* 文档卡渲染 Markdown 并支持复制；有来源时显示可折叠列表和正文引用角标。 */
export function DocumentCard({
  content,
  sources = [],
}: {
  content: string;
  sources?: CitationSource[];
}) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { activeKey, highlight } = useCitationHighlight();

  async function copy(): Promise<void> {
    setError(null);
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("复制未成,请手动选择文本。");
    }
  }

  return (
    <div className="space-y-1">
      <div className="px-1 text-md leading-relaxed text-foreground">
        {sources.length > 0 ? (
          <CitedDocumentText content={content} sources={sources} onCite={highlight} />
        ) : (
          <MarkdownText text={content} />
        )}
      </div>
      {/* 来源列表与正文角标使用相同的引用键。 */}
      <CitationSourcesPanel sources={sources} activeKey={activeKey} />
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="xs"
          onClick={() => void copy()}
          aria-label="复制答案"
          className="text-muted-foreground"
        >
          {copied ? <Check className="text-primary" aria-hidden /> : <Copy aria-hidden />}
          {copied ? "已复制" : "复制"}
        </Button>
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

/** 按空行分段渲染文档正文，并将纯文本段中的合法引用键转换为角标。 */
function CitedDocumentText({
  content,
  sources,
  onCite,
}: {
  content: string;
  sources: CitationSource[];
  onCite: (key: string) => void;
}) {
  const paragraphs = content.split(/\n\s*\n/);
  return (
    <div className="space-y-2">
      {paragraphs.map((para, i) => {
        const parts = renderWithCitations(para, sources, onCite);
        const hasMark = parts.some((p) => typeof p !== "string");
        if (!hasMark) {
          // 无引用段:原样 Markdown 渲染(保留表格/列表等结构)
          return <MarkdownText key={i} text={para} />;
        }
        // 含引用段:字符串与角标节点混排,MarkdownText 无法整体渲染,
        // 字符串段直接输出(该段放弃 Markdown 结构;引用句多为纯陈述,可接受)
        return (
          <p key={i} className="leading-relaxed">
            {parts.map((p, j) =>
              typeof p === "string" ? <span key={j}>{p}</span> : <span key={j}>{p}</span>,
            )}
          </p>
        );
      })}
    </div>
  );
}
