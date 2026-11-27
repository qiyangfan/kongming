/* 报告详情的章节呈现:章节标题/叙述、数据绑定表、绑定图表。 */

import { ChartView } from "@/components/chart-view";
import type { ChartSpec } from "@kongming/api-client/chart-shared";
import { MarkdownText } from "@kongming/ui/markdown-text";
import { ResultTable } from "@/components/result-table";
import type { ReportBinding, SectionOut } from "@kongming/api-client/report-api";
import { cellText } from "@kongming/api-client/utils";

/** 运行时弱类型的绑定收窄为强类型快照。 */
function binding(b: { [key: string]: unknown }): ReportBinding {
  return {
    sql: cellText(b.sql),
    status: cellText(b.status) || "OK",
    columns: Array.isArray(b.columns) ? (b.columns as string[]) : [],
    rows: Array.isArray(b.rows) ? (b.rows as unknown[][]) : [],
    row_count: Number(b.row_count ?? 0),
    truncated: Boolean(b.truncated),
    fail_reason: (b.fail_reason as string | null) ?? null,
  };
}

/** 单条数据绑定:成功时渲染结果表(截断时注明保留行数),失败时给出原因。 */
function BindingBlock({ raw }: { raw: { [key: string]: unknown } }) {
  const b = binding(raw);
  return (
    <div className="space-y-1">
      {b.status === "OK" ? (
        <>
          {b.columns.length > 0 && <ResultTable columns={b.columns} rows={b.rows} />}
          {b.truncated && (
            <p className="text-xs text-muted-foreground">
              结果截断:共 {b.row_count} 行,快照保留前 {b.rows.length} 行。
            </p>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">绑定失败:{b.fail_reason ?? "执行失败"}</p>
      )}
    </div>
  );
}

/** 章节图表:取首个成功绑定的数据作唯一候选。 */
function SectionChart({ section: s }: { section: SectionOut }) {
  const chart = s.chart as { type?: string; x?: string; y?: string } | null;
  const ok = s.bindings.map(binding).find((b) => b.status === "OK");
  if (!chart?.type || !chart.x || !chart.y || !ok) return null;
  const candidate: ChartSpec = { type: chart.type, x: chart.x, y: [chart.y], series: null };
  return (
    <div className="rounded-lg border border-border bg-card px-2 py-1">
      <ChartView candidates={[candidate]} data={{ columns: ok.columns, rows: ok.rows }} />
    </div>
  );
}

/** 章节块:一级章节用 h3、子章节用 h4;缺数/未完成状态如实标注。 */
export function SectionBlock({ section: s, depth }: { section: SectionOut; depth: 0 | 1 }) {
  const heading =
    depth === 0 ? (
      <h3 className="text-lg font-semibold text-foreground">{s.title}</h3>
    ) : (
      <h4 className="text-base font-semibold text-foreground">{s.title}</h4>
    );
  return (
    <section className="space-y-2" aria-label={s.title}>
      {heading}
      {s.data_status === "MISSING" && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
          数据缺失:{s.fail_reason ?? "执行失败"}
        </p>
      )}
      {s.data_status === "PENDING" && (
        <p role="status" className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
          未完成:{s.fail_reason ?? "预算耗尽或生成中断"}
        </p>
      )}
      {s.narrative_md && <MarkdownText text={s.narrative_md} />}
      {s.bindings.map((raw, i) => (
        <BindingBlock key={i} raw={raw} />
      ))}
      {s.chart && s.bindings.some((b) => b.status === "OK") && <SectionChart section={s} />}
    </section>
  );
}
