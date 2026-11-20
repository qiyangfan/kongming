/* 管理数据源：支持分页、搜索、创建、编辑、启停、删除和连接测试。编辑密码留空时沿用已保存的凭证；行级过滤依赖目标库的 create_by 列，注册账号应使用只读账号。 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/pagination";
import { PageHeader } from "@/components/page-header";
import { ErrorAlert } from "@/components/error-alert";
import {
  datasourceApi,
  type DatasourceItemOut,
  type TestConnectionOut,
} from "@/lib/datasource-api";
import { systemApi } from "@/lib/system-api";

import {
  DatasourceFormDialog,
  EMPTY_DATASOURCE_FORM,
  formFromDatasource,
  validateDatasourceForm,
  type DatasourceForm,
} from "./datasource-form-dialog";
import { DeleteDatasourceDialog, DatasourcesTable } from "./datasources-table";
import { useDatasourceMutations } from "./datasources-mutations";

const PAGE_SIZE = 10;

export default function DatasourcesPage() {
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<DatasourceItemOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<DatasourceForm>(EMPTY_DATASOURCE_FORM);
  const [formError, setFormError] = useState("");
  const [deleting, setDeleting] = useState<DatasourceItemOut | null>(null);
  const [testResult, setTestResult] = useState<TestConnectionOut | null>(null);
  const [toggleId, setToggleId] = useState<string | null>(null);
  const [toggleError, setToggleError] = useState("");

  const datasources = useQuery({
    queryKey: ["admin", "datasources", page, search],
    queryFn: () => datasourceApi.page(page, PAGE_SIZE, search),
  });
  // 租户下拉(创建时显式指定归属;复用租户只读清单)
  const tenants = useQuery({ queryKey: ["admin", "tenants"], queryFn: systemApi.listTenants });

  // 越界自愈:删除末页条目/过滤后总页数收缩时,回退到最后有效页
  useEffect(() => {
    const data = datasources.data;
    if (!data) return;
    const lastPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
    if (page > lastPage) setPage(lastPage);
  }, [datasources.data, page]);

  const mutations = useDatasourceMutations({
    form,
    editing,
    deleting,
    onFormError: setFormError,
    onCreated: () => setCreating(false),
    onUpdated: () => setEditing(null),
    onDeletingChange: setDeleting,
    onToggleState: (id, error) => {
      setToggleId(id);
      setToggleError(error);
    },
    onToggleError: setToggleError,
    onTestResult: setTestResult,
  });
  const { create, update, toggle, remove, testConn } = mutations;

  const openCreate = () => {
    setForm({ ...EMPTY_DATASOURCE_FORM, tenantId: tenants.data?.[0]?.id ?? "" });
    setFormError("");
    setTestResult(null);
    setCreating(true);
  };

  const openEdit = (d: DatasourceItemOut) => {
    setForm(formFromDatasource(d));
    setFormError("");
    setTestResult(null);
    setEditing(d);
  };

  const closeForm = () => {
    setCreating(false);
    setEditing(null);
  };

  const submit = () => {
    setFormError("");
    const error = validateDatasourceForm(form, creating);
    if (error) return setFormError(error);
    if (creating) create.mutate();
    else update.mutate();
  };

  const total = datasources.data?.total ?? 0;

  return (
    <div className="space-y-3">
      <PageHeader
        title="数据源"
        description="外部数据库连接的注册与管理;支持连通性测试与启停。"
        actions={
          <Button size="sm" onClick={openCreate}>
            注册数据源
          </Button>
        }
      />
      <DatasourceSearch
        keyword={keyword}
        setKeyword={setKeyword}
        onSearch={() => {
          setPage(1);
          setSearch(keyword.trim());
        }}
      />

      {datasources.isError && (
        <ErrorAlert text={`数据源列表加载失败:${datasources.error.message}`} />
      )}

      <DatasourcesTable
        rows={datasources.data?.items ?? []}
        isPending={datasources.isPending}
        togglePending={toggle.isPending}
        toggleId={toggleId}
        toggleError={toggleError}
        onToggle={(item) => toggle.mutate(item)}
        onEdit={openEdit}
        onDelete={setDeleting}
      />

      <Pagination
        page={page}
        pageCount={Math.max(1, Math.ceil(total / PAGE_SIZE))}
        total={total}
        onPageChange={setPage}
      />

      <DatasourceFormDialog
        open={creating || editing !== null}
        onClose={closeForm}
        creating={creating}
        form={form}
        setForm={setForm}
        formError={formError}
        tenantOptions={(tenants.data ?? []).map((t) => ({
          id: t.id,
          label: `${t.tenant_name}(${t.tenant_key})`,
        }))}
        testResult={testResult}
        testing={testConn.isPending}
        saving={create.isPending || update.isPending}
        onTest={() => {
          setTestResult(null);
          testConn.mutate();
        }}
        onSubmit={submit}
      />

      <DeleteDatasourceDialog
        deleting={deleting}
        onClose={() => setDeleting(null)}
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

/** 搜索框:回车或点按钮都按当前关键字查询(由调用方重置到第一页)。 */
function DatasourceSearch({
  keyword,
  setKeyword,
  onSearch,
}: {
  keyword: string;
  setKeyword: (value: string) => void;
  onSearch: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <Input
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onSearch();
        }}
        placeholder="搜索数据源键/名称,回车确认"
        className="max-w-64"
      />
      <Button variant="outline" size="sm" onClick={onSearch}>
        搜索
      </Button>
    </div>
  );
}
