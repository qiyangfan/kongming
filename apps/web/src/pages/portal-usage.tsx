/* 门户「当期用量」区:三项用量条 + 我的 API 密钥列表。 */

import type { UseQueryResult } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { ErrorAlert } from "@/components/error-alert";
import { DataView } from "@/components/data-view";
import { DIM_TEXT } from "@/lib/billing-labels";
import type { PortalKeyItem, PortalUsage } from "@/lib/billing-api";
import { li2yuan } from "@/lib/money";
import { formatDate, formatNumber } from "@/lib/datetime";

/** 单项用量条:额度为 0 视为不限;超预警线转警示色并提示。 */
function UsageBar({
  label,
  used,
  limit,
  warnPct,
  format,
}: {
  label: string;
  used: number;
  limit: number;
  warnPct: number;
  /** 值格式化(如金额维度传 li2yuan 转元);缺省千分位整数 */
  format?: (n: number) => string;
}) {
  const fmt = format ?? formatNumber;
  if (limit <= 0) {
    return (
      <div className="space-y-1">
        <p className="text-sm">
          {label}: {fmt(used)}(不限)
        </p>
      </div>
    );
  }
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const warn = used * 100 >= limit * warnPct;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground">
          {fmt(used)} / {fmt(limit)}({pct}%)
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full ${warn ? "bg-destructive" : "bg-primary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {warn && (
        <p role="alert" className="text-xs text-destructive">
          已达预警线({warnPct}%),请注意用量。
        </p>
      )}
    </div>
  );
}

/** 当期用量条组:按策略上限与预警线渲染 token/调用/费用三维。 */
function UsageBars({ usage }: { usage: PortalUsage }) {
  const policies = usage.policies ?? [];
  const limitOf = (dim: string) => policies.find((p) => p.dimension === dim)?.limit_value ?? 0;
  const warnOf = (dim: string) => policies.find((p) => p.dimension === dim)?.warn_pct ?? 80;
  return (
    <>
      <UsageBar
        label={DIM_TEXT.TOKENS}
        used={usage.tokens ?? 0}
        limit={limitOf("TOKENS")}
        warnPct={warnOf("TOKENS")}
      />
      <UsageBar
        label={DIM_TEXT.CALLS}
        used={usage.calls ?? 0}
        limit={limitOf("CALLS")}
        warnPct={warnOf("CALLS")}
      />
      <UsageBar
        label={DIM_TEXT.COST}
        used={usage.cost ?? 0}
        limit={limitOf("COST")}
        warnPct={warnOf("COST")}
        format={li2yuan}
      />
    </>
  );
}

/** 我的 API 密钥(仅显示生效中)。 */
function ApiKeysBlock({ keys }: { keys: UseQueryResult<PortalKeyItem[]> }) {
  return (
    <div className="space-y-1 pt-2">
      <p className="text-xs font-medium text-muted-foreground">我的 API 密钥(仅显示生效中)</p>
      {keys.isError ? (
        <ErrorAlert />
      ) : (
        <DataView
          rows={keys.data ?? []}
          rowKey={(k) => k.id}
          isLoading={keys.isPending}
          isEmpty={!keys.isPending && keys.data?.length === 0}
          emptyText="还没有密钥,到「管理 → 助手」页,在助手列表中点「创建密钥」。"
          columns={[
            {
              key: "bot_key",
              header: "密钥",
              cardPriority: 1,
              render: (k) => (
                <span className="font-mono text-xs text-muted-foreground">{k.bot_key}</span>
              ),
            },
            {
              key: "name",
              header: "名称",
              cardPriority: 2,
              render: (k) => <span className="text-xs text-muted-foreground">{k.name}</span>,
            },
            {
              key: "last_used_at",
              header: "最近使用",
              cardPriority: 3,
              cellClassName: "text-xs text-muted-foreground",
              render: (k) => (k.last_used_at ? `最近用 ${formatDate(k.last_used_at)}` : "未使用"),
            },
          ]}
        />
      )}
    </div>
  );
}

interface UsageSectionProps {
  usage: UseQueryResult<PortalUsage>;
  keys: UseQueryResult<PortalKeyItem[]>;
}

/** 「当期用量」整区:标题、读取状态与用量条、密钥清单。 */
export function UsageSection({ usage, keys }: UsageSectionProps) {
  return (
    <section className="space-y-3 rounded-lg border border-border p-4">
      <h2 className="text-sm font-medium">当期用量({usage.data?.month ?? "…"})</h2>
      {(() => {
        if (usage.isError) return <ErrorAlert />;
        if (usage.isLoading) {
          return <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />;
        }
        return (
          <>
            <UsageBars usage={usage.data!} />
            <ApiKeysBlock keys={keys} />
          </>
        );
      })()}
    </section>
  );
}
