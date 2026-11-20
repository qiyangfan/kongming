/* 会话内一键生成报告：对话框开关、表单状态与发起请求。 */

import { useState } from "react";
import { useNavigate } from "react-router";

import { generateReport } from "@/lib/report-api";

export interface ReportDialogApi {
  open: boolean;
  setOpen: (open: boolean) => void;
  instruction: string;
  setInstruction: (value: string) => void;
  title: string;
  setTitle: (value: string) => void;
  error: string | null;
  busy: boolean;
  submit: () => Promise<void>;
}

/** 报告指令表单；发起成功后跳转报告详情。sessionIdRef 供异步回调读最新会话。 */
export function useReportDialog(
  datasource: string | null,
  sessionIdRef: { readonly current: string | null },
): ReportDialogApi {
  const navigate = useNavigate();
  // 包装成对象再传：宽化的 ref.current 直接下传会被推断成属性类型报 null 不兼容
  const sessionForRequest = (): { session_id: string | null } => ({
    session_id: sessionIdRef.current,
  });
  const [open, setOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /** 校验指令后发起生成，成功关闭对话框并进入报告详情。 */
  async function submit(): Promise<void> {
    const trimmed = instruction.trim();
    if (busy || !trimmed) return;
    setBusy(true);
    setError(null);
    try {
      const out = await generateReport({
        instruction: trimmed,
        // 未选数据源时省略字段,由服务端取默认库
        ...(datasource ? { datasource_key: datasource } : {}),
        ...sessionForRequest(),
        title: title.trim() || null,
      });
      setOpen(false);
      setInstruction("");
      setTitle("");
      void navigate(`/reports/${out.report_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "发起生成未成,请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  return { open, setOpen, instruction, setInstruction, title, setTitle, error, busy, submit };
}
