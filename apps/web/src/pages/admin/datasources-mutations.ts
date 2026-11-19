/* 数据源的写操作:新建、更新、启停、删除与连接测试。编辑时密码留空表示沿用已保存的凭证。 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  datasourceApi,
  type DatasourceItemOut,
  type TestConnectionOut,
} from "@/lib/datasource-api";

import type { DatasourceForm } from "./datasource-form-dialog";

interface UseDatasourceMutationsParams {
  form: DatasourceForm;
  editing: DatasourceItemOut | null;
  deleting: DatasourceItemOut | null;
  onFormError: (message: string) => void;
  onCreated: () => void;
  onUpdated: () => void;
  onDeletingChange: (item: DatasourceItemOut | null) => void;
  onToggleState: (id: string | null, error: string) => void;
  onToggleError: (message: string) => void;
  onTestResult: (result: TestConnectionOut) => void;
}

/** 把表单转成创建/更新请求体;密码空串在更新时转 null 表示不改。 */
function toUpdateBody(form: DatasourceForm) {
  return {
    datasource_key: form.datasourceKey,
    tenant_id: form.tenantId,
    name: form.name.trim(),
    host: form.host.trim(),
    port: Number(form.port),
    database: form.database.trim(),
    username: form.username.trim(),
    password: form.password || null,
    default_schema: form.defaultSchema,
    readonly: form.readonly,
  };
}

/** 数据源页的全部写操作,mutationFn 在触发时读取最新表单。 */
export function useDatasourceMutations(params: UseDatasourceMutationsParams) {
  const qc = useQueryClient();
  const { form, editing, deleting } = params;

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "datasources"] });
    // 选项接口(对话页切换器)同失效:启停/删除即时反映到用户面
    void qc.invalidateQueries({ queryKey: ["datasource-options"] });
  };

  const create = useMutation({
    mutationFn: () =>
      datasourceApi.create({
        datasource_key: form.datasourceKey.trim(),
        name: form.name.trim(),
        tenant_id: form.tenantId,
        host: form.host.trim(),
        port: Number(form.port),
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password,
        default_schema: form.defaultSchema.trim(),
        readonly: form.readonly,
      }),
    onSuccess: () => {
      params.onCreated();
      invalidate();
    },
    onError: (e: Error) => params.onFormError(e.message),
  });

  const update = useMutation({
    mutationFn: () => datasourceApi.update(editing!.id, toUpdateBody(form)), // 密码留空=不改
    onSuccess: () => {
      params.onUpdated();
      invalidate();
    },
    onError: (e: Error) => params.onFormError(e.message),
  });

  const toggle = useMutation({
    mutationFn: (item: DatasourceItemOut) =>
      item.status === "ACTIVE" ? datasourceApi.disable(item.id) : datasourceApi.enable(item.id),
    onMutate: (item) => params.onToggleState(item.id, ""),
    onSuccess: () => {
      params.onToggleState(null, "");
      invalidate();
    },
    onError: (e: Error) => params.onToggleError(e.message), // 启停失败不得静默,收敛为行内提示
  });

  const remove = useMutation({
    mutationFn: () => datasourceApi.remove(deleting!.id),
    onSuccess: () => {
      params.onDeletingChange(null);
      invalidate();
    },
  });

  const testConn = useMutation({
    mutationFn: () =>
      datasourceApi.testConnection({
        id: editing?.id ?? null,
        host: form.host.trim(),
        port: Number(form.port),
        database: form.database.trim(),
        username: form.username.trim(),
        password: form.password || null, // 编辑态留空 → 服务端取库存密文补位
      }),
    onSuccess: (result) => params.onTestResult(result),
    onError: (e: Error) => params.onTestResult({ ok: false, reason: e.message }),
  });

  return { create, update, toggle, remove, testConn };
}
