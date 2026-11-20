/* 问答和报告详情共用的结果表；动态列在窄屏内横向滚动。 */

import { cellText } from "@/lib/utils";

/** 单元格展示文本与 title:空值显示破折号,其余按弱类型转文本。 */
function displayCell(cell: unknown): { text: string; title: string | undefined } {
  if (cell === null || cell === undefined) return { text: "—", title: undefined };
  const text = cellText(cell);
  return { text, title: text || undefined };
}

export function ResultTable({
  columns,
  rows,
  labels,
}: {
  columns: string[];
  rows: unknown[][];
  labels?: string[];
}) {
  return (
    <div className="overflow-x-auto overscroll-x-contain rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            {columns.map((c, i) => (
              <th
                key={`${c}-${i}`}
                title={c}
                className="px-3 py-2 text-left font-medium whitespace-nowrap text-muted-foreground"
              >
                {labels?.[i] || c}
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
                {row.map((cell, j) => {
                  const c = displayCell(cell);
                  return (
                    <td
                      key={j}
                      className="max-w-80 truncate px-3 py-1.5 text-foreground"
                      title={c.title}
                    >
                      {c.text}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
