/* assistant 结果卡片(web-nav spec「对话区渲染」):
   摘要/表格/SQL 折叠/洞察/统计;SUCCESS/REJECTED/FAILED 三态视觉区分。 */

import { ChevronRight, Database, Lightbulb, ShieldOff, TriangleAlert } from "lucide-react";

import { ChartView } from "@/components/chart-view";
import type { SessionMessageOut } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

/* 生成类型的 result 字段皆可选:本地归一化收窄 */
function normalize(msg: SessionMessageOut): {
  columns: string[];
  rows: unknown[][];
  insights: { [key: string]: string }[];
  stats: Record<string, unknown>;
  chart: { type: string; x: string | null; y: string | null };
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
    return (
      <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
        <p className="text-foreground">{msg.error ?? "查询失败,请换个问法试试"}</p>
      </div>
    );
  }

  const result = normalize(msg);
  return (
    <div className="space-y-3">
      {msg.summary && (
        <p className="px-1 text-[15px] leading-relaxed text-foreground">{msg.summary}</p>
      )}

      {result.chart.type !== "none" && result.columns.length > 0 && (
        <div className="rounded-lg border border-border bg-card px-2 py-1">
          <ChartView chart={result.chart} data={{ columns: result.columns, rows: result.rows }} />
        </div>
      )}

      {result.columns.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                {result.columns.map((c) => (
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
              {result.rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={Math.max(result.columns.length, 1)}
                    className="px-3 py-6 text-center text-muted-foreground"
                  >
                    查无数据,秋叶落,结果空。
                  </td>
                </tr>
              ) : (
                result.rows.map((row, i) => (
                  <tr key={i} className="border-t border-border">
                    {row.map((cell, j) => (
                      <td key={j} className="px-3 py-1.5 whitespace-nowrap text-foreground">
                        {cell === null || cell === undefined ? "—" : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {msg.sql && (
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
      )}

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
    </div>
  );
}
