/* 写工具人工确认卡(tool-calling spec「写工具人工确认」):
   响应 pending_tool_calls 非空时渲染;确认/拒绝 → POST /chat/tool-confirm →
   回执消息落会话(invalidate messages 刷新可见);本地剔除已决项防重复点击。 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldQuestion, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, type PendingToolCallOut, type ToolConfirmOut } from "@/lib/api";
import { useDatasource } from "@/lib/datasource-store";
import { useTenant } from "@/lib/tenant-store";

/* 工具名 → 中文短语(与 apps/api toolcall/registry.py 内置注册表对齐; MCP 动态工具等未收录名回退显示原名)。 */
const TOOL_LABELS: Record<string, string> = {
  validate_sql: "校验 SQL",
  execute_sql: "执行 SQL 查询",
  rag_retrieve: "检索知识库",
  create_system_announcement: "发布系统公告",
  run_python_code: "运行 Python 分析代码",
};

interface ToolConfirmCardProps {
  pending: PendingToolCallOut[];
  sessionId: string;
  /** 全部待确认项已决(确认/拒绝)后回调——父组件清除确认卡状态 */
  onAllResolved?: () => void;
}

export function ToolConfirmCard({ pending, sessionId, onAllResolved }: ToolConfirmCardProps) {
  const qc = useQueryClient();
  const tenant = useTenant((s) => s.current);
  const datasource = useDatasource((s) => s.current);
  // 已决项本地剔除(回执经 refetch 呈现);失败行内提示
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const decide = useMutation({
    mutationFn: (input: { confirm_id: string; decision: "confirm" | "reject" }) =>
      api.post<ToolConfirmOut>("/chat/tool-confirm", {
        confirm_id: input.confirm_id,
        decision: input.decision,
        session_id: sessionId,
      }),
    onSuccess: (data, input) => {
      const next = new Set(resolved).add(input.confirm_id);
      setResolved(next);
      // 过期/条件变化等结局行内即时反馈;执行与拒绝的回执经 refetch 呈现
      if (data.status === "executed" || data.status === "confirm_rejected") {
        setError(null);
      } else if (data.status === "confirm_expired") {
        setError("该确认已过期,操作未执行。");
      } else if (data.status === "rejected") {
        setError("执行条件已变化(如权限调整),操作未执行。");
      } else {
        setError("执行失败,请稍后重试。");
      }
      // 回执消息已落会话:失效消息与侧栏(标题可能已被回执轮触发改写)
      void qc.invalidateQueries({
        queryKey: ["messages", sessionId, tenant, datasource],
      });
      void qc.invalidateQueries({ queryKey: ["sessions", tenant] });
      if (next.size >= pending.length) onAllResolved?.();
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "操作失败,请重试");
    },
  });

  const visible = pending.filter((p) => !resolved.has(p.confirm_id));
  if (visible.length === 0) return null;

  return (
    <div
      className="space-y-2 rounded-lg border border-accent/50 bg-accent/10 px-4 py-3"
      role="group"
      aria-label="待确认操作"
    >
      <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
        <ShieldQuestion className="size-4 text-accent-foreground" aria-hidden />
        待你确认的操作
      </div>
      {visible.map((p) => (
        <div key={p.confirm_id} className="space-y-1.5">
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground">
              {TOOL_LABELS[p.tool_name] ?? p.tool_name}
            </span>
            {p.args_digest ? `: ${p.args_digest}` : ""}
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ confirm_id: p.confirm_id, decision: "confirm" })}
            >
              {decide.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认执行
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ confirm_id: p.confirm_id, decision: "reject" })}
            >
              拒绝
            </Button>
          </div>
        </div>
      ))}
      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
