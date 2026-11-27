/* 展示问答轮次的汇总、趋势、切片和明细；NO_DATA 单独呈现，trace_id 可打开用量时间线。 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { PageHeader } from "@kongming/ui/page-header";
import { useTheme } from "@kongming/api-client/theme-store";
import { api } from "@kongming/api-client/api";
import {
  qualityExportUrl,
  qualityPage,
  qualitySummary,
  type QualityRoundItemOut,
} from "@kongming/api-client/quality-api";
import { downloadCsv, filterParams } from "./quality-page-meta";
import { QualitySummarySection, useDatasourceOptions } from "./quality-page-summary";
import {
  buildRowActions,
  QualityDetailSection,
  type QualityFiltersState,
} from "./quality-page-detail";
import {
  ContextDialog,
  ScoreDetailDialog,
  StageTraceDialog,
  TimelineDialog,
} from "./quality-page-dialogs";

const PAGE_SIZE = 20;

// 导出文件名带当日日期,便于按日归档
function exportFilename(): string {
  return `问答质量-${new Date().toISOString().slice(0, 10)}.csv`;
}

export default function QualityPage() {
  const { theme } = useTheme();
  const navigate = useNavigate();
  const [days, setDays] = useState(30);
  const [status, setStatus] = useState("");
  const [datasourceKey, setDatasourceKey] = useState("");
  const [fallback, setFallback] = useState("");
  const [page, setPage] = useState(1);
  const [timelineTrace, setTimelineTrace] = useState<string | null>(null);
  const [scoreMessageId, setScoreMessageId] = useState<string | null>(null);
  const [contextMessageId, setContextMessageId] = useState<string | null>(null);
  const [stageTraceId, setStageTraceId] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(false);

  const filters = { status, datasourceKey, fallback };
  const summary = useQuery({
    queryKey: ["quality", "summary", days],
    queryFn: () => qualitySummary(days),
  });
  const list = useQuery({
    queryKey: ["quality", "page", page, status, datasourceKey, fallback],
    queryFn: () => qualityPage({ page, size: PAGE_SIZE, ...filterParams(filters) }),
  });

  // 当前页被清空时退回上一页,避免落在空页。
  useEffect(() => {
    if (list.data && list.data.items.length === 0 && list.data.total > 0 && page > 1) {
      setPage((p) => Math.max(1, p - 1));
    }
  }, [list.data, page]);

  const dsOptions = useDatasourceOptions(summary);
  const totalPages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE));

  const onCopy = (row: QualityRoundItemOut) => {
    void navigator.clipboard.writeText(row.question ?? "");
    setCopied(row.id);
  };
  const rowActions = buildRowActions({
    onCopy,
    onContext: setContextMessageId,
    onStageTrace: setStageTraceId,
    onRag: () => {
      void navigate("/rag");
    },
  });

  async function doExport() {
    setExporting(true);
    setExportError(false);
    try {
      await downloadCsv(qualityExportUrl(filterParams(filters)), exportFilename(), async (url) =>
        (await api.getBlob(url)).blob(),
      );
    } catch {
      setExportError(true);
    } finally {
      setExporting(false);
    }
  }

  // 改任一筛选即回第一页
  const onFilterChange = (key: keyof QualityFiltersState, v: string) => {
    const setters = { status: setStatus, datasourceKey: setDatasourceKey, fallback: setFallback };
    setters[key](v);
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="问答质量"
        description="线上每一轮的四态占比、未覆盖分布与 prompt 版本切片——真实质量,不再靠猜。"
      />

      <QualitySummarySection
        summary={summary}
        theme={theme}
        days={days}
        onDaysChange={setDays}
        exporting={exporting}
        onExport={() => void doExport()}
      />

      <QualityDetailSection
        list={list}
        filters={filters}
        dsOptions={dsOptions}
        onFilterChange={onFilterChange}
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        rowActions={rowActions}
        lowScore={summary.data?.overview.low_score_threshold ?? 60}
        copied={copied}
        exportError={exportError}
        onScore={setScoreMessageId}
        onTimeline={setTimelineTrace}
      />

      <TimelineDialog traceId={timelineTrace} onClose={() => setTimelineTrace(null)} />
      <ScoreDetailDialog messageId={scoreMessageId} onClose={() => setScoreMessageId(null)} />
      <ContextDialog messageId={contextMessageId} onClose={() => setContextMessageId(null)} />
      <StageTraceDialog messageId={stageTraceId} onClose={() => setStageTraceId(null)} />
    </div>
  );
}
