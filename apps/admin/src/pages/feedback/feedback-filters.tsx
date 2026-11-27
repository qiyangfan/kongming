/* 反馈页的列定义与筛选栏:评分/处置状态/租户/数据源/时间范围/仅看纠错。 */

import { Badge } from "@kongming/ui/badge";
import { Checkbox } from "@kongming/ui/checkbox";
import type { DataViewColumn } from "@kongming/ui/data-view";
import { Label } from "@kongming/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { StatusBadge, type StatusMeta } from "@kongming/ui/status-badge";
import { formatDateTime } from "@kongming/api-client/datetime";
import { displayUser } from "@kongming/api-client/display-user";
import type { FeedbackPageItemOut } from "@kongming/api-client/feedback-api";

const STATUS_META: StatusMeta = {
  PENDING: { label: "待处置", variant: "outline" },
  ADOPTED: { label: "已采纳", variant: "default" },
  REJECTED: { label: "已驳回", variant: "secondary" },
  DISMISSED: { label: "已忽略", variant: "secondary" },
};

// 评分列(徽章统一走 StatusBadge;空值显示 —)
const RATING_META: StatusMeta = {
  UP: { label: "有用", variant: "secondary" },
  DOWN: { label: "不准确", variant: "default" },
};

/** 筛选值 ALL 表示不过滤,转成空串交给查询参数。 */
function orEmpty(v: string): string {
  return v === "ALL" ? "" : v;
}

export function feedbackColumns(): DataViewColumn<FeedbackPageItemOut>[] {
  return [
    {
      key: "feedback_time",
      header: "时间",
      cardPriority: 3,
      cellClassName: "whitespace-nowrap text-muted-foreground",
      render: (item) => formatDateTime(item.feedback_time),
    },
    {
      key: "user",
      header: "用户",
      cardPriority: 2,
      render: (item) => (
        <span
          className="block max-w-32 truncate"
          title={displayUser(item.user_nickname, item.user_username)}
        >
          {displayUser(item.user_nickname, item.user_username)}
        </span>
      ),
    },
    { key: "tenant_key", header: "租户", render: (item) => item.tenant_key },
    { key: "datasource_key", header: "数据源", render: (item) => item.datasource_key },
    {
      key: "rating",
      header: "评分",
      cardPriority: 4,
      // rating 为窄联合:有值才出徽章,空值纯文本「—」
      render: (item) =>
        item.rating ? <StatusBadge meta={RATING_META} value={item.rating} /> : "—",
    },
    {
      key: "question",
      header: "问句",
      cardPriority: 1,
      render: (item) => (
        <span className="block max-w-56 truncate" title={item.question ?? undefined}>
          {item.question ?? "—"}
        </span>
      ),
    },
    {
      key: "corrected_sql",
      header: "纠错",
      render: (item) => (item.corrected_sql ? <Badge variant="outline">带纠错</Badge> : "—"),
    },
    {
      key: "feedback_status",
      header: "处置状态",
      cardPriority: 5,
      render: (item) => <StatusBadge meta={STATUS_META} value={item.feedback_status} />,
    },
  ];
}

type TenantRow = { id: string; tenant_name: string };
type DsRow = { datasource_key: string; name: string };

export function FeedbackFilters({
  rating,
  onRatingChange,
  status,
  onStatusChange,
  tenantId,
  onTenantChange,
  tenantSource,
  dsKey,
  onDsKeyChange,
  dsSource,
  days,
  onDaysChange,
  onlyCorrected,
  onOnlyCorrectedChange,
}: {
  rating: string;
  onRatingChange: (v: string) => void;
  status: string;
  onStatusChange: (v: string) => void;
  tenantId: string;
  onTenantChange: (v: string) => void;
  tenantSource: { isSuccess: boolean; data?: TenantRow[] | undefined };
  dsKey: string;
  onDsKeyChange: (v: string) => void;
  dsSource: { isSuccess: boolean; data?: DsRow[] | undefined };
  days: number;
  onDaysChange: (v: number) => void;
  onlyCorrected: boolean;
  onOnlyCorrectedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">评分</Label>
        <Select value={rating || "ALL"} onValueChange={(v) => onRatingChange(orEmpty(v))}>
          <SelectTrigger className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部</SelectItem>
            <SelectItem value="UP">有用</SelectItem>
            <SelectItem value="DOWN">不准确</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">处置状态</Label>
        <Select value={status || "ALL"} onValueChange={(v) => onStatusChange(orEmpty(v))}>
          <SelectTrigger className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部</SelectItem>
            <SelectItem value="PENDING">待处置</SelectItem>
            <SelectItem value="ADOPTED">已采纳</SelectItem>
            <SelectItem value="REJECTED">已驳回</SelectItem>
            <SelectItem value="DISMISSED">已忽略</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {tenantSource.isSuccess && (
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">租户</Label>
          <Select value={tenantId || "ALL"} onValueChange={(v) => onTenantChange(orEmpty(v))}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">全部</SelectItem>
              {(tenantSource.data ?? []).map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.tenant_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">数据源</Label>
        <Select value={dsKey || "ALL"} onValueChange={(v) => onDsKeyChange(orEmpty(v))}>
          <SelectTrigger className="w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部</SelectItem>
            {(dsSource.data ?? []).map((o) => (
              <SelectItem key={o.datasource_key} value={o.datasource_key}>
                {o.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">时间范围</Label>
        <Select value={String(days)} onValueChange={(v) => onDaysChange(Number(v))}>
          <SelectTrigger className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="7">近 7 天</SelectItem>
            <SelectItem value="30">近 30 天</SelectItem>
            <SelectItem value="90">近 90 天</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <label className="flex items-center gap-2 pb-1.5 text-sm">
        <Checkbox
          checked={onlyCorrected}
          onCheckedChange={(c) => onOnlyCorrectedChange(c === true)}
        />
        仅看带纠错
      </label>
    </div>
  );
}
