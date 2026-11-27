/* 当前展示结果的行导出按钮（CSV / Excel），复用展示表头。 */

import { useState } from "react";
import { Download, Loader2 } from "lucide-react";

import { exportResult } from "@kongming/api-client/chat-api";
import { Button } from "@kongming/ui/button";

function exportErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "导出未成,请稍后再试。";
}

/* 导出当前展示的列和行；表头优先使用业务标签，失败时在卡片内提示。 */
export function ExportButtons({
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
      setError(exportErrorMessage(err));
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
