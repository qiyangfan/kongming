/* 管理 HTTP 连接器的声明、凭证掩码、启停和测试调用；凭证只写入，不回显。 */

import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { Button } from "@kongming/ui/button";
import { ErrorAlert } from "@kongming/ui/error-alert";
import { PageHeader } from "@kongming/ui/page-header";
import {
  listConnectors,
  type ConnectorOut,
  type TestCallOut,
} from "@kongming/api-client/connector-api";
import { emptyForm, rowToForm, type FormState, type TestSnapshot } from "./connectors-page-model";
import { ConnectorsTable } from "./connectors-page-table";
import { ConnectorFormDialog } from "./connectors-page-form";
import { ConnectorTestDialog } from "./connectors-page-test-dialog";
import { ConnectorDeleteDialog } from "./connectors-page-delete";
import {
  runConnectorTest,
  snapshotFromForm,
  snapshotFromRow,
  useConnectorInvalidate,
  useConnectorMutations,
} from "./connectors-page-actions";

export default function ConnectorsPage() {
  const [form, setForm] = useState<FormState | null>(null);
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<ConnectorOut | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [toggleError, setToggleError] = useState<Record<string, string>>({});
  // 测试调用:显式确认 → 真实出站 → 结果展示
  const [testSnap, setTestSnap] = useState<TestSnapshot | null>(null);
  const [testArgs, setTestArgs] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<TestCallOut | null>(null);
  const [testError, setTestError] = useState("");
  // mutation 回调里读最新值,避免闭包捕获旧状态
  const formRef = useRef(form);
  formRef.current = form;
  const deletingRef = useRef(deleting);
  deletingRef.current = deleting;
  const snapRef = useRef(testSnap);
  snapRef.current = testSnap;
  const argsRef = useRef(testArgs);
  argsRef.current = testArgs;

  const connectors = useQuery({ queryKey: ["admin", "connectors"], queryFn: listConnectors });
  const invalidate = useConnectorInvalidate();

  const { save, toggle, remove } = useConnectorMutations({
    form: () => formRef.current,
    deletingId: () => deletingRef.current?.id ?? null,
    onSaved: () => {
      setForm(null);
      setFormError("");
    },
    onFormError: setFormError,
    onToggleError: setToggleError,
    onDeleted: () => {
      setDeleting(null);
      setDeleteError("");
    },
    onDeleteError: setDeleteError,
    invalidate,
  });

  const runTest = useMutation({
    mutationFn: async () => {
      const snap = snapRef.current;
      if (!snap) return null;
      return await runConnectorTest(snap, argsRef.current);
    },
    onSuccess: (r) => {
      if (r) setTestResult(r);
    },
    onError: (e) => setTestError(e instanceof Error ? e.message : "测试调用失败"),
  });

  // 两类入口共用的重置:清空入参、旧结果与错误后打开确认框
  const openTest = (snap: TestSnapshot) => {
    setTestArgs({});
    setTestResult(null);
    setTestError("");
    setTestSnap(snap);
  };

  const rows = connectors.data ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="连接器"
        description="租户自助注册 HTTP 端点为 Agent 工具:出口主机白名单 + SSRF 防护 + 凭证加密落库(任何界面不回显明文,仅尾号掩码)+ 全量审计。注册/启停即时生效。"
        actions={
          <Button
            onClick={() => {
              setForm(emptyForm());
              setFormError("");
            }}
          >
            <Plus aria-hidden />
            注册连接器
          </Button>
        }
      />

      {connectors.isError && <ErrorAlert />}

      <ConnectorsTable
        rows={rows}
        isLoading={connectors.isPending}
        isEmpty={!connectors.isPending && !connectors.isError && rows.length === 0}
        togglePending={toggle.isPending}
        togglePendingId={toggle.variables?.rowId}
        toggleError={toggleError}
        onToggle={(row) => toggle.mutate({ rowId: row.id, enabled: row.enabled !== "ENABLED" })}
        onTest={(row) => openTest(snapshotFromRow(row))}
        onEdit={(row) => {
          setForm(rowToForm(row));
          setFormError("");
        }}
        onDelete={(row) => {
          setDeleting(row);
          setDeleteError("");
        }}
      />

      <ConnectorFormDialog
        form={form}
        formError={formError}
        savePending={save.isPending}
        testPending={runTest.isPending}
        onChange={setForm}
        onClose={() => setForm(null)}
        onSave={() => save.mutate()}
        onTest={() => form && openTest(snapshotFromForm(form))}
      />

      <ConnectorDeleteDialog
        target={deleting}
        error={deleteError}
        pending={remove.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => remove.mutate()}
      />

      <ConnectorTestDialog
        snapshot={testSnap}
        args={testArgs}
        onArgChange={(name, v) => setTestArgs({ ...testArgs, [name]: v })}
        result={testResult}
        error={testError}
        pending={runTest.isPending}
        onClose={() => setTestSnap(null)}
        onRun={() => runTest.mutate()}
      />
    </div>
  );
}
