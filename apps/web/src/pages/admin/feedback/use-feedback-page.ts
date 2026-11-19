/* 反馈页的状态、查询与变更逻辑;页面只负责组装。 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/lib/auth-store";
import { datasourceApi } from "@/lib/datasource-api";
import { feedbackOverview, feedbackPage } from "@/lib/feedback-api";
import { hasPerm } from "@/lib/nav";
import { systemApi } from "@/lib/system-api";

export const PAGE_SIZE = 20;

export function useFeedbackPageState() {
  const { user } = useAuth();
  const canHandle = hasPerm(user, "system:feedback:handle");
  const [days, setDays] = useState(30);
  const [rating, setRating] = useState("");
  const [status, setStatus] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [dsKey, setDsKey] = useState("");
  const [onlyCorrected, setOnlyCorrected] = useState(false);
  const [page, setPage] = useState(1);

  // 租户清单挂 system:user:manage——仅 view 权限者 403,降级隐藏该筛选项
  const tenants = useQuery({
    queryKey: ["system", "tenants"],
    queryFn: systemApi.listTenants,
    retry: false,
  });
  const dsOptions = useQuery({
    queryKey: ["feedback", "ds-options"],
    queryFn: datasourceApi.options,
  });

  const overview = useQuery({
    queryKey: ["feedback", "overview", days, tenantId, dsKey],
    queryFn: () =>
      feedbackOverview({
        days,
        ...(tenantId ? { tenant_id: tenantId } : {}),
        ...(dsKey ? { datasource_key: dsKey } : {}),
      }),
  });

  const list = useQuery({
    queryKey: ["feedback", "page", page, rating, status, tenantId, dsKey, onlyCorrected],
    queryFn: () =>
      feedbackPage({
        page,
        size: PAGE_SIZE,
        ...(rating ? { rating: rating as "UP" | "DOWN" } : {}),
        ...(status
          ? { feedback_status: status as "PENDING" | "ADOPTED" | "REJECTED" | "DISMISSED" }
          : {}),
        ...(tenantId ? { tenant_id: tenantId } : {}),
        ...(dsKey ? { datasource_key: dsKey } : {}),
        ...(onlyCorrected ? { only_corrected: true } : {}),
      }),
  });

  return {
    canHandle,
    days,
    setDays,
    rating,
    setRating,
    status,
    setStatus,
    tenantId,
    setTenantId,
    dsKey,
    setDsKey,
    onlyCorrected,
    setOnlyCorrected,
    page,
    setPage,
    tenants,
    dsOptions,
    overview,
    list,
  };
}
