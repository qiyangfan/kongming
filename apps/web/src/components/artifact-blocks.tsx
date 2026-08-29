/* 类型化产物卡(026 conversation-artifacts spec「类型化渲染器」):
   sql 卡=可编辑代码框+「重新执行」(rerun 端点六层防御,结果落新消息,
   invalidate messages 前缀与 014 反馈同范式);chart 卡=调参面板(标题/轴名,
   类型切换在 ChartView 自身 toggle,全部本地重绘不调 LLM);document 卡=
   Markdown 渲染+复制。无 artifacts 的消息不走这里(assistant-card 回落既有形态)。 */

import { useId, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  Copy,
  Database,
  Loader2,
  Play,
  SlidersHorizontal,
} from "lucide-react";

import { ChartView, type ChartDatum, type ChartTune } from "@/components/chart-view";
import { MarkdownText } from "@/components/markdown-text";
import { rerunArtifact } from "@/lib/chat-api";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/* sql 卡(spec「SQL 卡再执行」):编辑仅改本地草稿,点「重新执行」才把
   当前草稿交服务端复检执行;结果作为新消息出现在会话尾部(不覆盖本卡)。
   messageId 缺失(017 乐观快照容错分支空串)时不渲染执行入口——非 UUID
   直传会 422 白点(与 EXPLAIN/反馈同一守卫判据) */
export function SqlArtifactCard({
  messageId,
  sql,
}: {
  messageId: string;
  sql: string;
}) {
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
      // 新消息由 refetch 入列(会话尾部);前缀失效与反馈回显同范式
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
              <Button
                variant="outline"
                size="xs"
                onClick={() => void run()}
                disabled={busy}
              >
                {busy ? (
                  <Loader2 className="animate-spin" aria-hidden />
                ) : (
                  <Play aria-hidden />
                )}
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

/* chart 卡调参面板(spec「图表调参重绘」):标题/轴名本地 state 透传
   ChartView 即时重绘,零网络调用;类型切换沿用 ChartView 内建 toggle */
export function ChartArtifactCard({
  chart,
  data,
}: {
  chart: { type: string; x: string | null; y: string | null };
  data: ChartDatum;
}) {
  const [tune, setTune] = useState<ChartTune>({ title: "", xName: "", yName: "" });
  // R1-F6:同页多张 chart 卡,id 硬编码会重复(点 Label 聚焦到首卡);
  // useId 后缀保证每卡唯一
  const uid = useId();

  return (
    <div className="rounded-lg border border-border bg-card px-2 py-1">
      <ChartView chart={chart} data={data} tune={tune} />
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
                placeholder={chart.x ?? "留空不显示"}
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
                placeholder={chart.y ?? "留空不显示"}
                className="h-8 text-xs"
              />
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

/* document 卡(spec):Markdown 渲染(子集,原始 HTML 不解析)+ 一键复制 */
export function DocumentCard({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        <MarkdownText text={content} />
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="xs"
          onClick={() => void copy()}
          aria-label="复制答案"
          className="text-muted-foreground"
        >
          {copied ? (
            <Check className="text-primary" aria-hidden />
          ) : (
            <Copy aria-hidden />
          )}
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
