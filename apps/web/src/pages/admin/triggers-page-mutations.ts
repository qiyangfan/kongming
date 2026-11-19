/* 触发器页写操作:保存(创建/更新)、启停与删除,成功后统一失效列表缓存。 */

import type { Dispatch, SetStateAction } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createTrigger,
  deleteTrigger,
  setTriggerEnabled,
  updateTrigger,
  type TriggerItem,
} from "@/lib/trigger-api";
import { buildUpdatePayload, taskFormInvalid, type FormState } from "./triggers-page-model";

type SaveResult = { webhook_path: string | null; secret?: string } | null;

type Args = {
  form: FormState | null;
  deleting: TriggerItem | null;
  onSaved: (out: SaveResult) => void;
  onSaveError: (msg: string) => void;
  // 启停错误按行 id 记录;空串表示清除该行错误
  onToggleError: Dispatch<SetStateAction<Record<string, string>>>;
  onDeleted: () => void;
  onDeleteError: (msg: string) => void;
};

export function useTriggerMutations(args: Args) {
  const qc = useQueryClient();
  const { form, deleting } = args;
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["admin", "triggers"] });

  const save = useMutation({
    mutationFn: async (): Promise<SaveResult> => {
      if (!form) return null;
      if (taskFormInvalid(form)) {
        args.onSaveError("数据任务须填写目标模板并选择数据源");
        return null;
      }
      const payload = buildUpdatePayload(form);
      if (form.id === null) {
        return await createTrigger({
          ...payload,
          tg_key: form.tgKey.trim(),
          type: form.type,
          secret: form.secret,
        });
      }
      await updateTrigger(form.id, payload);
      return null;
    },
    onSuccess: (out) => {
      args.onSaved(out);
      invalidate();
    },
    onError: (e) => {
      // TARGET_NOT_FOUND 等后端 detail 已在 lib/api 统一转译为中文
      args.onSaveError(e instanceof Error ? e.message : "保存失败");
    },
  });

  const toggle = useMutation({
    mutationFn: async ({ row, enabled }: { row: TriggerItem; enabled: boolean }) => {
      await setTriggerEnabled(row.id, enabled);
    },
    onSuccess: (_d, v) => {
      args.onToggleError((m) => ({ ...m, [v.row.id]: "" }));
      invalidate();
    },
    onError: (e, v) =>
      args.onToggleError((m) => ({
        ...m,
        [v.row.id]: e instanceof Error ? e.message : "操作失败",
      })),
  });

  const remove = useMutation({
    mutationFn: async () => {
      if (deleting) await deleteTrigger(deleting.id);
    },
    onSuccess: () => {
      args.onDeleted();
      invalidate();
    },
    onError: (e) => args.onDeleteError(e instanceof Error ? e.message : "删除失败"),
  });

  return { save, toggle, remove };
}
