/* MCP 服务的删除确认框与模板只读预览框。 */

import { Eye, Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { FormRow } from "@/components/form-row";
import { Input } from "@/components/ui/input";
import type { McpPromptEntryOut, McpPromptPreviewOut, McpServerOut } from "@/lib/mcp-api";

export function DeleteMcpDialog({
  server,
  error,
  pending,
  onConfirm,
  onClose,
}: {
  server: McpServerOut | null;
  error: string;
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <AlertDialog
      open={server !== null}
      onOpenChange={(o) => {
        if (pending) return;
        if (!o) onClose();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除 MCP 服务「{server?.name}」?</AlertDialogTitle>
          <AlertDialogDescription>
            删除后其全部工具立即从注册面消失,已绑定这些工具的 Bot 运行时调用将被拒。
            此操作不可恢复。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>再想想</AlertDialogCancel>
          <Button variant="destructive" disabled={pending} onClick={onConfirm}>
            {pending && <Loader2 className="animate-spin" aria-hidden />}
            确认删除
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/* 模板只读预览:按名实时拉取渲染结果,服务端已过注入检测+大小上限;
   只读展示,不注入任何提示词、非运行时提示来源 */
export function PromptPreviewDialog({
  prompt,
  args,
  onArgsChange,
  data,
  error,
  loading,
  canFetch,
  onFetch,
  onClose,
}: {
  prompt: McpPromptEntryOut | null;
  args: Record<string, string>;
  onArgsChange: (args: Record<string, string>) => void;
  data: McpPromptPreviewOut | null;
  error: string;
  loading: boolean;
  canFetch: boolean;
  onFetch: () => void;
  onClose: () => void;
}) {
  return (
    <AlertDialog
      open={prompt !== null}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <AlertDialogContent className="sm:max-w-xl">
        <AlertDialogHeader>
          <AlertDialogTitle>模板预览「{prompt?.name}」</AlertDialogTitle>
          <AlertDialogDescription>
            只读渲染结果(实时拉取,不落库);内容已过注入检测,超 64KB 截断。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {prompt && (prompt.arguments ?? []).length > 0 && (
          <div className="space-y-2">
            {(prompt.arguments ?? []).map((a) => (
              <FormRow
                key={a.name}
                label={`${a.name}${a.required ? " *" : ""}`}
                htmlFor={`pv-${a.name}`}
              >
                <Input
                  id={`pv-${a.name}`}
                  value={args[a.name] ?? ""}
                  onChange={(e) => onArgsChange({ ...args, [a.name]: e.target.value })}
                  aria-label={`参数 ${a.name}`}
                />
              </FormRow>
            ))}
          </div>
        )}
        <PreviewResult data={data} error={error} loading={loading} />
        <AlertDialogFooter>
          <AlertDialogCancel>关闭</AlertDialogCancel>
          <Button disabled={loading || !canFetch} onClick={onFetch}>
            {loading ? <Loader2 className="animate-spin" aria-hidden /> : <Eye aria-hidden />}
            {data !== null ? "重新拉取" : "拉取预览"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** 预览结果区:拉取中/拉取异常/服务端失败/渲染文本(含注入命中与截断提示)。 */
function PreviewResult({
  data,
  error,
  loading,
}: {
  data: McpPromptPreviewOut | null;
  error: string;
  loading: boolean;
}) {
  return (
    <>
      {loading && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <Loader2 className="animate-spin" aria-hidden />
          拉取中…
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {data && !data.ok && (
        <p role="alert" className="text-sm text-destructive">
          拉取失败({data.kind}):{data.detail}
        </p>
      )}
      {data?.ok && (
        <div className="space-y-1.5">
          {data.injection_hits.length > 0 && (
            <p className="text-xs text-destructive">
              内容命中注入检测已净化:{data.injection_hits.join(", ")}
            </p>
          )}
          {data.truncated && <p className="text-xs text-muted-foreground">内容超 64KB 已截断。</p>}
          <pre className="max-h-72 overflow-y-auto rounded-md border bg-muted/40 p-3 text-xs whitespace-pre-wrap">
            {data.text}
          </pre>
        </div>
      )}
    </>
  );
}
