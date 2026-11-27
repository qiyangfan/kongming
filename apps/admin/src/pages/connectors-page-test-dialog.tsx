/* 测试调用确认弹窗:按声明参数填参、真实出站并展示结果摘要。 */

import { FlaskConical, Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@kongming/ui/alert-dialog";
import { Badge } from "@kongming/ui/badge";
import { Button } from "@kongming/ui/button";
import { Input } from "@kongming/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@kongming/ui/select";
import { FormRow } from "@kongming/ui/form-row";
import type { TestCallOut } from "@kongming/api-client/connector-api";
import type { TestSnapshot } from "./connectors-page-model";

type ArgDef = TestSnapshot["argNames"][number];

function ArgField({
  arg,
  value,
  onChange,
}: {
  arg: ArgDef;
  value: string;
  onChange: (v: string) => void;
}) {
  if (arg.type === "boolean") {
    return (
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={`ta-${arg.name}`} aria-label={`参数 ${arg.name}`}>
          <SelectValue placeholder="省略" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="true">true</SelectItem>
          <SelectItem value="false">false</SelectItem>
        </SelectContent>
      </Select>
    );
  }
  return (
    <Input
      id={`ta-${arg.name}`}
      value={value}
      inputMode={arg.type === "number" ? "decimal" : "text"}
      onChange={(e) => onChange(e.target.value)}
      aria-label={`参数 ${arg.name}`}
    />
  );
}

// 结果区:成功展示状态码与耗时,失败展示分类;正文摘要过长时滚动
function TestResultView({ result }: { result: TestCallOut }) {
  return (
    <div className="space-y-1.5 text-xs">
      {result.ok ? (
        <p className="flex flex-wrap items-center gap-2">
          <Badge>HTTP {result.status_code ?? "-"}</Badge>
          <span className="text-muted-foreground">耗时 {result.duration_ms}ms</span>
        </p>
      ) : (
        <p role="alert" className="text-destructive">
          调用失败({result.failure ?? "未知分类"});耗时 {result.duration_ms}ms
        </p>
      )}
      {result.body_summary && (
        <pre className="max-h-48 overflow-y-auto rounded-md border bg-muted/40 p-2 whitespace-pre-wrap">
          {result.body_summary}
        </pre>
      )}
    </div>
  );
}

export function ConnectorTestDialog({
  snapshot,
  args,
  onArgChange,
  result,
  error,
  pending,
  onClose,
  onRun,
}: {
  snapshot: TestSnapshot | null;
  args: Record<string, string>;
  onArgChange: (name: string, v: string) => void;
  result: TestCallOut | null;
  error: string;
  pending: boolean;
  onClose: () => void;
  onRun: () => void;
}) {
  return (
    <AlertDialog
      open={snapshot !== null}
      onOpenChange={(o) => {
        if (pending) return;
        if (!o) onClose();
      }}
    >
      <AlertDialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>测试调用「{snapshot?.title}」?</AlertDialogTitle>
          <AlertDialogDescription>
            将按当前声明向目标端点发起一次真实出站(落工具审计);确认后执行。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {snapshot && snapshot.argNames.length > 0 && (
          <div className="space-y-2">
            {snapshot.argNames.map((a) => (
              <FormRow
                key={a.name}
                label={`${a.name}${a.required ? " *" : ""}`}
                htmlFor={`ta-${a.name}`}
              >
                <ArgField
                  arg={a}
                  value={args[a.name] ?? ""}
                  onChange={(v) => onArgChange(a.name, v)}
                />
              </FormRow>
            ))}
          </div>
        )}
        {pending && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="animate-spin" aria-hidden />
            出站中…
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {result && <TestResultView result={result} />}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>关闭</AlertDialogCancel>
          <Button disabled={pending || snapshot === null} onClick={onRun}>
            {pending ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : (
              <FlaskConical aria-hidden />
            )}
            {result !== null ? "重新调用" : "确认调用"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
