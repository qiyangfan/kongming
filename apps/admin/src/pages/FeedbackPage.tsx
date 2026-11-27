/* 反馈分析页提供统计、趋势、筛选和详情查看;图表颜色使用设计 token。 */

import { useEffect, useState } from "react";

import { DataView } from "@kongming/ui/data-view";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { PageHeader } from "@kongming/ui/page-header";
import { Pagination } from "@kongming/ui/pagination";
import type { FeedbackPageItemOut } from "@kongming/api-client/feedback-api";

import { FeedbackDetailDialog } from "./feedback/FeedbackDetailDialog";
import { FeedbackFilters, feedbackColumns } from "./feedback/feedback-filters";
import { FeedbackStats } from "./feedback/FeedbackStats";
import { PAGE_SIZE, useFeedbackPageState } from "./feedback/use-feedback-page";

export default function FeedbackPage() {
  const s = useFeedbackPageState();
  const [detail, setDetail] = useState<FeedbackPageItemOut | null>(null);

  // 删除或处置后重新计算页码，避免落在空页。
  const { data: listData } = s.list;
  const { setPage, page } = s;
  useEffect(() => {
    if (listData && listData.items.length === 0 && listData.total > 0 && page > 1) {
      setPage((p) => Math.max(1, p - 1));
    }
  }, [listData, page, setPage]);

  const totalPages = Math.max(1, Math.ceil((s.list.data?.total ?? 0) / PAGE_SIZE));
  // 筛选回调统一归回第 1 页
  const withReset = (fn: (v: string) => void) => (v: string) => {
    fn(v);
    s.setPage(1);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="反馈分析"
        description="用户反馈的统计与明细——处置反馈、沉淀纠错,让军师越答越准。"
      />

      <FeedbackStats overview={s.overview} />

      <FeedbackFilters
        rating={s.rating}
        onRatingChange={withReset(s.setRating)}
        status={s.status}
        onStatusChange={withReset(s.setStatus)}
        tenantId={s.tenantId}
        onTenantChange={withReset(s.setTenantId)}
        tenantSource={s.tenants}
        dsKey={s.dsKey}
        onDsKeyChange={withReset(s.setDsKey)}
        dsSource={s.dsOptions}
        days={s.days}
        onDaysChange={s.setDays}
        onlyCorrected={s.onlyCorrected}
        onOnlyCorrectedChange={(c) => {
          s.setOnlyCorrected(c);
          s.setPage(1);
        }}
      />

      {s.list.isError && <ErrorAlert />}
      <DataView
        rows={s.list.data?.items ?? []}
        rowKey={(item) => item.message_id}
        isLoading={s.list.isLoading}
        isEmpty={!s.list.isLoading && !s.list.isError && (s.list.data?.items.length ?? 0) === 0}
        emptyText="暂无反馈——问答卡片右下角即可赞踩。"
        columns={feedbackColumns()}
        actions={[{ label: "详情", onSelect: setDetail }]}
      />

      <Pagination
        page={s.page}
        pageCount={totalPages}
        total={s.list.data?.total ?? 0}
        onPageChange={s.setPage}
      />

      {detail && (
        <FeedbackDetailDialog
          item={detail}
          canHandle={s.canHandle}
          onClose={() => setDetail(null)}
          onHandled={() => {
            setDetail(null);
            void s.list.refetch();
            void s.overview.refetch();
          }}
        />
      )}
    </div>
  );
}
