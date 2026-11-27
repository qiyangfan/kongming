/* 工作流定义可在画布和 JSON 间编辑；画布无法表达的定义会留在 JSON 态，避免切换时丢字段。 */

import { forwardRef, useCallback, useImperativeHandle, useState } from "react";
import { Braces, TriangleAlert, Workflow } from "lucide-react";

import { Textarea } from "@kongming/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@kongming/ui/toggle-group";
import {
  dagToCanvas,
  moveCanvasPositions,
  type DagToCanvasResult,
} from "@kongming/api-client/workflow-canvas";
import { WorkflowCanvasPanel } from "./workflow-canvas-panel";

export interface WorkflowDefinitionEditorHandle {
  /** 取当前定义(画布态直接回传;JSON 态先过语法解析,失败返回错误文案) */
  getDefinition():
    { ok: true; definition: unknown } | { ok: false; error: string };
  /** 保存成功后把布局坐标迁移到新版本键(localStorage 按 wf_key+version) */
  notifySaved(newVersion: number): void;
}

interface WorkflowDefinitionEditorProps {
  /** 打开时的定义(编辑=行内 definition;新建=空图) */
  initialDefinition: unknown;
  /** 布局持久化键的 wf_key(新建=输入框实时值,空串入 "_" 桶) */
  wfKey: string;
  /** 布局持久化键的版本(新建=0) */
  version: number;
  /** 服务端校验错误文案(空串=无;画布态据此高亮定位节点) */
  errorText: string;
}

type EditorMode = "canvas" | "json";

export const WorkflowDefinitionEditor = forwardRef<
  WorkflowDefinitionEditorHandle,
  WorkflowDefinitionEditorProps
>(function WorkflowDefinitionEditor(
  { initialDefinition, wfKey, version, errorText },
  ref,
) {
  // 打开时一次判定画布可表达性并定初始形态;dagToCanvas 不随 JSON 输入重跑
  const [initialTo] = useState<DagToCanvasResult>(() =>
    dagToCanvas(initialDefinition),
  );
  const [mode, setMode] = useState<EditorMode>(
    initialTo.ok ? "canvas" : "json",
  );
  const [definition, setDefinition] = useState<unknown>(initialDefinition);
  const [jsonDraft, setJsonDraft] = useState(() =>
    JSON.stringify(initialDefinition, null, 2),
  );
  const [jsonError, setJsonError] = useState("");
  const [unsupported, setUnsupported] = useState<string[]>(
    initialTo.ok ? [] : initialTo.reasons,
  );
  const [initToken, setInitToken] = useState(1);

  const tryParseDraft = useCallback(
    (
      text: string,
    ): { ok: true; definition: unknown } | { ok: false; error: string } => {
      try {
        return { ok: true, definition: JSON.parse(text) as unknown };
      } catch (e) {
        return {
          ok: false,
          error: `JSON 解析失败: ${e instanceof Error ? e.message : String(e)}`,
        };
      }
    },
    [],
  );

  const switchToCanvas = useCallback(() => {
    const parsed = tryParseDraft(jsonDraft);
    if (!parsed.ok) {
      setJsonError(parsed.error);
      return; // 语法错误:阻断切换,停留 JSON
    }
    const to = dagToCanvas(parsed.definition);
    if (!to.ok) {
      setUnsupported(to.reasons);
      return; // 不支持形态:明示原因,阻断切换(fail-safe,零丢弃)
    }
    setDefinition(parsed.definition);
    setUnsupported([]);
    setJsonError("");
    setInitToken((t) => t + 1);
    setMode("canvas");
  }, [jsonDraft, tryParseDraft]);

  const switchToJson = useCallback(() => {
    setJsonDraft(JSON.stringify(definition, null, 2));
    setMode("json");
  }, [definition]);

  // JSON 态实时解析:合法即同步进定义对象(保存/切画布共用同一份)
  const handleDraftChange = useCallback(
    (text: string) => {
      setJsonDraft(text);
      const parsed = tryParseDraft(text);
      if (parsed.ok) {
        setDefinition(parsed.definition);
        setJsonError("");
      } else {
        setJsonError(parsed.error);
      }
    },
    [tryParseDraft],
  );

  useImperativeHandle(
    ref,
    () => ({
      getDefinition: () => {
        if (mode === "canvas") return { ok: true, definition };
        return tryParseDraft(jsonDraft);
      },
      notifySaved: (newVersion: number) =>
        moveCanvasPositions(wfKey, version, newVersion),
    }),
    [mode, definition, jsonDraft, tryParseDraft, wfKey, version],
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={mode}
          onValueChange={(v) => {
            if (v === mode || v === "") return;
            if (v === "canvas") switchToCanvas();
            else switchToJson();
          }}
          aria-label="定义编辑形态"
        >
          <ToggleGroupItem value="canvas">
            <Workflow className="size-3.5" aria-hidden />
            画布
          </ToggleGroupItem>
          <ToggleGroupItem value="json">
            <Braces className="size-3.5" aria-hidden />
            JSON
          </ToggleGroupItem>
        </ToggleGroup>
        <p className="hidden text-xs text-muted-foreground sm:block">
          {mode === "canvas"
            ? "拖线连接依赖;条件节点出边需指定 on 分支。"
            : "高级形态:手工编辑 JSON DAG,保存时服务端结构校验。"}
        </p>
      </div>

      {mode === "canvas" ? (
        <WorkflowCanvasPanel
          definition={definition}
          initToken={initToken}
          wfKey={wfKey}
          version={version}
          errorText={errorText}
          onDefinitionChange={setDefinition}
        />
      ) : (
        <div className="space-y-2">
          {unsupported.length > 0 && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-md border border-warning/40 bg-accent p-3 text-xs text-foreground"
            >
              <TriangleAlert
                className="mt-0.5 size-3.5 shrink-0 text-warning"
                aria-hidden
              />
              <span>
                画布暂不支持当前定义的以下形态,已停留在 JSON 编辑(未做任何改写):
                <span className="mt-1 block list-inside list-disc">
                  {unsupported.map((r) => (
                    <span key={r} className="block">
                      {r}
                    </span>
                  ))}
                </span>
              </span>
            </div>
          )}
          <Textarea
            value={jsonDraft}
            onChange={(e) => handleDraftChange(e.target.value)}
            rows={14}
            spellCheck={false}
            aria-label="工作流定义(JSON DAG)"
            className="font-mono text-xs leading-relaxed"
          />
          {jsonError && (
            <p role="alert" className="text-sm text-destructive">
              {jsonError}
            </p>
          )}
        </div>
      )}
    </div>
  );
});
