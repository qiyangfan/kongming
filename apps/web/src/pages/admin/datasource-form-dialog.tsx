/* 数据源注册/编辑对话框:连接参数表单、连接测试与保存。 */

import { Checkbox } from "@/components/ui/checkbox";
import { FormRow } from "@/components/form-row";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { DatasourceItemOut, TestConnectionOut } from "@/lib/datasource-api";

export type DatasourceForm = {
  datasourceKey: string;
  name: string;
  tenantId: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  defaultSchema: string;
  readonly: boolean;
};

export const EMPTY_DATASOURCE_FORM: DatasourceForm = {
  datasourceKey: "",
  name: "",
  tenantId: "",
  host: "",
  port: "5432",
  database: "",
  username: "",
  password: "",
  defaultSchema: "public",
  readonly: true,
};

/** 编辑态回填:密钥与密码留空,密码留空即沿用库存密文。 */
export function formFromDatasource(d: DatasourceItemOut): DatasourceForm {
  return {
    datasourceKey: d.datasource_key,
    name: d.name,
    tenantId: d.tenant_id,
    host: d.host,
    port: String(d.port),
    database: d.database,
    username: d.username,
    password: "",
    defaultSchema: d.default_schema,
    readonly: d.readonly,
  };
}

/** 校验表单;通过时返回 null,否则返回首条错误文案。 */
export function validateDatasourceForm(form: DatasourceForm, creating: boolean): string | null {
  if (creating && !/^[a-z][a-z0-9_]{1,63}$/.test(form.datasourceKey.trim())) {
    return "数据源键须以小写字母开头,可含数字与下划线,长度 2 到 64 个字符";
  }
  if (!form.name.trim()) return "名称不可为空";
  if (!form.tenantId) return "必须选择归属租户";
  if (!form.host.trim() || !form.database.trim() || !form.username.trim()) {
    return "主机/库名/用户名不可为空";
  }
  const port = Number(form.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return "端口不合法";
  if (creating && !form.password) return "密码不可为空";
  return null;
}

interface DatasourceFormDialogProps {
  open: boolean;
  onClose: () => void;
  creating: boolean;
  form: DatasourceForm;
  setForm: (form: DatasourceForm) => void;
  formError: string;
  tenantOptions: { id: string; label: string }[];
  testResult: TestConnectionOut | null;
  testing: boolean;
  saving: boolean;
  onTest: () => void;
  onSubmit: () => void;
}

/** 新建/编辑数据源对话框。 */
export function DatasourceFormDialog(props: DatasourceFormDialogProps) {
  const { creating, form, setForm, testResult } = props;
  const patch = (p: Partial<DatasourceForm>) => setForm({ ...form, ...p });
  return (
    <Dialog open={props.open} onOpenChange={(open) => !open && props.onClose()}>
      <DialogContent aria-describedby={undefined} className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{creating ? "注册数据源" : `编辑数据源 ${form.datasourceKey}`}</DialogTitle>
        </DialogHeader>
        {props.formError && (
          <p className="text-destructive text-sm" role="alert">
            {props.formError}
          </p>
        )}
        <form
          className="grid gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            props.onSubmit();
          }}
        >
          <DatasourceConnFields creating={creating} form={form} patch={patch} />
          <DatasourceTenantSchemaFields
            creating={creating}
            form={form}
            patch={patch}
            tenantOptions={props.tenantOptions}
          />
          <DatasourceHostFields form={form} patch={patch} />
          <DatasourceDatabaseFields form={form} patch={patch} editing={!creating} />
          {testResult && (
            <p
              className={
                testResult.ok ? "text-sm text-muted-foreground" : "text-destructive text-sm"
              }
              role="status"
            >
              {testResult.ok ? "连接成功" : `连接失败:${testResult.reason}`}
            </p>
          )}
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              type="button"
              variant="secondary"
              disabled={props.testing}
              onClick={props.onTest}
            >
              {props.testing ? "测试中…" : "测试连接"}
            </Button>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={props.onClose}>
                取消
              </Button>
              <Button type="submit" disabled={props.saving}>
                {props.saving ? "保存中…" : "保存"}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** 数据源键与名称两列。 */
function DatasourceConnFields({
  creating,
  form,
  patch,
}: {
  creating: boolean;
  form: DatasourceForm;
  patch: (p: Partial<DatasourceForm>) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <FormRow
        label="数据源键"
        htmlFor="ds-key"
        help="小写字母开头,可含数字与下划线;创建后不可改。"
      >
        <Input
          id="ds-key"
          value={form.datasourceKey}
          disabled={!creating}
          placeholder="如 sakila"
          onChange={(e) => patch({ datasourceKey: e.target.value })}
        />
      </FormRow>
      <FormRow label="名称" htmlFor="ds-name">
        <Input id="ds-name" value={form.name} onChange={(e) => patch({ name: e.target.value })} />
      </FormRow>
    </div>
  );
}

/** 归属租户与默认 schema。 */
function DatasourceTenantSchemaFields({
  creating,
  form,
  patch,
  tenantOptions,
}: {
  creating: boolean;
  form: DatasourceForm;
  patch: (p: Partial<DatasourceForm>) => void;
  tenantOptions: { id: string; label: string }[];
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <FormRow label="归属租户">
        <Select
          value={form.tenantId}
          disabled={!creating}
          onValueChange={(v) => patch({ tenantId: v })}
        >
          <SelectTrigger>
            <SelectValue placeholder="选择租户(创建后不可改)" />
          </SelectTrigger>
          <SelectContent>
            {tenantOptions.map((t) => (
              <SelectItem key={t.id} value={t.id}>
                {t.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FormRow>
      <FormRow label="默认 schema" htmlFor="ds-schema">
        <Input
          id="ds-schema"
          value={form.defaultSchema}
          disabled={!creating}
          onChange={(e) => patch({ defaultSchema: e.target.value })}
        />
      </FormRow>
    </div>
  );
}

/** 主机与端口(主机占两列宽)。 */
function DatasourceHostFields({
  form,
  patch,
}: {
  form: DatasourceForm;
  patch: (p: Partial<DatasourceForm>) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <FormRow label="主机" htmlFor="ds-host" className="col-span-2">
        <Input id="ds-host" value={form.host} onChange={(e) => patch({ host: e.target.value })} />
      </FormRow>
      <FormRow label="端口" htmlFor="ds-port">
        <Input
          id="ds-port"
          inputMode="numeric"
          value={form.port}
          onChange={(e) => patch({ port: e.target.value })}
        />
      </FormRow>
    </div>
  );
}

/** 库名/用户名与密码(含只读账号提示)、只读开关。 */
function DatasourceDatabaseFields({
  form,
  patch,
  editing,
}: {
  form: DatasourceForm;
  patch: (p: Partial<DatasourceForm>) => void;
  editing: boolean;
}) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <FormRow label="库名" htmlFor="ds-database">
          <Input
            id="ds-database"
            value={form.database}
            onChange={(e) => patch({ database: e.target.value })}
          />
        </FormRow>
        <FormRow label="用户名" htmlFor="ds-username">
          <Input
            id="ds-username"
            value={form.username}
            onChange={(e) => patch({ username: e.target.value })}
          />
        </FormRow>
      </div>
      <FormRow label={`密码${editing ? "(留空不改)" : ""}`} htmlFor="ds-password">
        <Input
          id="ds-password"
          type="password"
          value={form.password}
          onChange={(e) => patch({ password: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          请使用只读账号。如需按部门控制数据可见范围,业务表须有记录创建人的 create_by 字段。
        </p>
      </FormRow>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={form.readonly}
          onCheckedChange={(checked) => patch({ readonly: checked === true })}
        />
        只读数据源(仅登记供 AI 问答查询;平台侧另有连接级只读保护)
      </label>
    </>
  );
}
