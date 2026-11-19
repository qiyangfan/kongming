/* 表单字段对应服务端定义；本地提示只辅助填写，最终校验由服务端执行，错误按 configKey 定位到字段。 */

import { useState } from "react";

import { NODE_ID_RE, RESERVED_NODE_IDS, type WfNodeType } from "@/lib/workflow-canvas";
import {
  ConditionOnField,
  ConfigFieldRow,
  FIELDS,
  INT_RE,
  NodeHeader,
  NodeIdField,
  type FieldContext,
} from "./node-config-fields";

interface NodeConfigFormProps {
  nodeId: string;
  nodeType: WfNodeType;
  config: Record<string, unknown>;
  /** 目标节点的条件入边 on 值;null = 无条件入边(不显示该字段) */
  conditionOn: "true" | "false" | null;
  /** 服务端校验错误(已定位到本节点) */
  serverError: { message: string; configKey?: string } | null;
  takenIds: readonly string[];
  onConfigChange: (config: Record<string, unknown>) => void;
  onIdChange: (id: string) => void;
  onOnValueChange: (on: "true" | "false") => void;
  onDelete: () => void;
}

/* 节点 id 本地校验：未改动不报；空值、字符集、保留名、重名依次判定。 */
function validateId(idDraft: string, nodeId: string, takenIds: readonly string[]): string | null {
  if (idDraft === nodeId) return null;
  if (idDraft.length === 0) return "节点 id 不能为空";
  if (!NODE_ID_RE.test(idDraft)) return "仅字母/数字/下划线";
  if (RESERVED_NODE_IDS.has(idDraft)) return "与保留名冲突";
  if (takenIds.includes(idDraft)) return "与其他节点 id 重复";
  return null;
}

export function NodeConfigForm({
  nodeId,
  nodeType,
  config,
  conditionOn,
  serverError,
  takenIds,
  onConfigChange,
  onIdChange,
  onOnValueChange,
  onDelete,
}: NodeConfigFormProps) {
  const [idDraft, setIdDraft] = useState(nodeId);
  const [jsonDrafts, setJsonDrafts] = useState<Record<string, string>>({});
  const [numDraft, setNumDraft] = useState<string>(
    typeof config.max_iterations === "number" ? String(config.max_iterations) : "",
  );

  const setField = (key: string, value: unknown | undefined) => {
    const next = { ...config };
    if (value === undefined) delete next[key];
    else next[key] = value;
    onConfigChange(next);
  };

  const idError = validateId(idDraft, nodeId, takenIds);

  const setJsonDraft = (key: string, text: string, parsed: unknown) => {
    setJsonDrafts((d) => ({ ...d, [key]: text }));
    if (parsed === undefined && text === "") setField(key, undefined);
    else if (parsed !== undefined) setField(key, parsed);
  };

  const setNum = (raw: string) => {
    setNumDraft(raw);
    if (raw === "") setField("max_iterations", undefined);
    else if (INT_RE.test(raw)) setField("max_iterations", Number(raw));
  };

  const ctx: FieldContext = {
    config,
    setField,
    jsonDrafts,
    setJsonDraft,
    numDraft,
    setNumDraft: setNum,
  };

  return (
    <div className="space-y-3">
      <NodeHeader nodeType={nodeType} nodeId={nodeId} onDelete={onDelete} />

      <NodeIdField
        nodeId={nodeId}
        idDraft={idDraft}
        idError={idError}
        setIdDraft={setIdDraft}
        onIdChange={onIdChange}
      />

      {conditionOn !== null && <ConditionOnField value={conditionOn} onChange={onOnValueChange} />}

      {FIELDS[nodeType].map((f) => (
        <ConfigFieldRow
          key={f.key}
          f={f}
          serverMessage={serverError?.configKey === f.key ? serverError.message : null}
          ctx={ctx}
        />
      ))}

      {serverError && !serverError.configKey && (
        <p role="alert" className="text-xs text-destructive">
          {serverError.message}
        </p>
      )}
    </div>
  );
}
