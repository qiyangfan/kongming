/* 连接器页写操作与测试快照装配:保存/启停/删除,以及行级与表单级测试声明。 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createConnector,
  deleteConnector,
  setConnectorEnabled,
  testConnector,
  updateConnector,
} from "@/lib/connector-api";
import { formToBody, rowToForm, type FormState, type TestSnapshot } from "./connectors-page-model";

const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

type Handlers = {
  form: () => FormState | null;
  deletingId: () => string | null;
  onSaved: () => void;
  onFormError: (m: string) => void;
  // 启停错误按行 id 记录;空串表示清除该行错误
  onToggleError: (fn: (m: Record<string, string>) => Record<string, string>) => void;
  onDeleted: () => void;
  onDeleteError: (m: string) => void;
  invalidate: () => void;
};

export function useConnectorMutations(h: Handlers) {
  return {
    save: useMutation({
      mutationFn: async () => {
        const form = h.form();
        if (!form) return;
        const body = formToBody(form);
        if (form.id === null) {
          await createConnector(body);
          return;
        }
        await updateConnector(form.id, body);
      },
      onSuccess: () => {
        h.onSaved();
        h.invalidate();
      },
      onError: (e) => h.onFormError(errText(e, "保存失败")),
    }),
    toggle: useMutation({
      mutationFn: async (v: { rowId: string; enabled: boolean }) => {
        await setConnectorEnabled(v.rowId, v.enabled);
      },
      onSuccess: (_d, v) => h.onToggleError((m) => ({ ...m, [v.rowId]: "" })),
      onError: (e, v) => h.onToggleError((m) => ({ ...m, [v.rowId]: errText(e, "操作失败") })),
    }),
    remove: useMutation({
      mutationFn: async () => {
        const id = h.deletingId();
        if (id) await deleteConnector(id);
      },
      onSuccess: () => {
        h.onDeleted();
        h.invalidate();
      },
      onError: (e) => h.onDeleteError(errText(e, "删除失败")),
    }),
  };
}

// 测试结果缓存失效与连接器列表同前缀,便于统一刷新
export function useConnectorInvalidate() {
  const qc = useQueryClient();
  return () => void qc.invalidateQueries({ queryKey: ["admin", "connectors"] });
}

// 声明参数按名装配入参:number 转数值(失败保留原文),boolean 转 true/false;
// 空值不携带——与 LLM 调用的入参形态一致
function buildTestArgs(
  argNames: TestSnapshot["argNames"],
  raw: Record<string, string>,
): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const a of argNames) {
    const v = (raw[a.name] ?? "").trim();
    if (v === "") continue;
    if (a.type === "number") {
      const n = Number(v);
      args[a.name] = Number.isNaN(n) ? v : n;
    } else if (a.type === "boolean") {
      args[a.name] = v === "true";
    } else {
      args[a.name] = v;
    }
  }
  return args;
}

// 行级测试:以该行已保存声明出站,凭证沿用存量
export function snapshotFromRow(row: Parameters<typeof rowToForm>[0]): TestSnapshot {
  return {
    title: row.name,
    body: { ...formToBody(rowToForm(row)), use_saved_secret: true },
    argNames: row.params.map((p) => ({
      name: String(p.name ?? ""),
      type: String(p.type ?? "string"),
      required: Boolean(p.required),
    })),
  };
}

// 表单级测试:编辑态凭证留空=用存量凭证测试(先测后存);新建态材料随表单携带
export function snapshotFromForm(form: FormState): TestSnapshot {
  return {
    title: form.name || form.key,
    body: {
      ...formToBody(form),
      use_saved_secret: form.id !== null && form.authSecret.trim() === "",
    },
    argNames: form.params
      .filter((p) => p.name.trim())
      .map((p) => ({ name: p.name.trim(), type: p.type, required: p.required })),
  };
}

export async function runConnectorTest(snapshot: TestSnapshot, rawArgs: Record<string, string>) {
  return testConnector({
    ...snapshot.body,
    args: buildTestArgs(snapshot.argNames, rawArgs),
    use_saved_secret: true,
  });
}
