/* 计费管理页(034,超管):套餐 CRUD + 租户配额策略 CRUD + 绑套餐 +
   全部账单概览。
   035:套餐 U/D(绑定中 409 行内可见)+策略删除确认(013 模式)+数值
   受控校验(非法行内报错,禁静默归零)+BOT 级策略(scope/Bot 下拉,
   数据源=billing 自供清单,解耦 user:manage/bot:manage)+账单分页
   (支付状态列+作废)。 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { QueryErrorAlert } from "@/components/query-error";
import {
  bindPlan,
  createPlan,
  deletePlan,
  deletePolicy,
  getTenantPlan,
  listBillingTenants,
  listBillingBots,
  listPlans,
  listPolicies,
  pageAdminInvoices,
  updatePlan,
  upsertPolicy,
  voidInvoice,
  type PlanItem,
} from "@/lib/billing-api";

/** 数值受控校验(035 挂账⑦):非负整数才合法,非法不归零——返回 null 交行内报错 */
function parseNonNegInt(raw: string): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const n = Number(raw);
  return Number.isSafeInteger(n) ? n : null;
}

const EMPTY_PLAN_FORM = {
  plan_key: "",
  name: "",
  monthly_tokens: "0",
  monthly_calls: "0",
  price_per_mtok: "0",
  price_per_call: "0",
};

export default function BillingPage() {
  const qc = useQueryClient();
  const [tenantId, setTenantIdRaw] = useState("");
  // R1-T3:切租户连带重置策略表单(防他租户 scope_ref 残留脏态)
  const setTenantId = (v: string) => {
    setTenantIdRaw(v);
    setPolicyForm({
      dimension: "TOKENS", limit_value: "0", warn_pct: "80",
      scope: "TENANT", scope_ref: "",
    });
    setPolicyError("");
  };
  const [planForm, setPlanForm] = useState(EMPTY_PLAN_FORM);
  const [planError, setPlanError] = useState("");
  const [editPlan, setEditPlan] = useState<PlanItem | null>(null);
  const [editForm, setEditForm] = useState(EMPTY_PLAN_FORM);
  const [editError, setEditError] = useState("");
  const [deletePlanTarget, setDeletePlanTarget] = useState<PlanItem | null>(null);
  const [deletePlanError, setDeletePlanError] = useState("");
  const [policyForm, setPolicyForm] = useState({
    dimension: "TOKENS",
    limit_value: "0",
    warn_pct: "80",
    scope: "TENANT",
    scope_ref: "",
  });
  const [policyError, setPolicyError] = useState("");
  const [deletePolicyId, setDeletePolicyId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [voidTarget, setVoidTarget] = useState<string | null>(null);
  const [voidError, setVoidError] = useState("");
  const PAGE_SIZE = 20;

  const tenants = useQuery({ queryKey: ["admin", "billing", "tenants"], queryFn: listBillingTenants });
  const plans = useQuery({ queryKey: ["admin", "billing", "plans"], queryFn: listPlans });
  const policies = useQuery({
    queryKey: ["admin", "billing", "policies", tenantId],
    queryFn: () => listPolicies(tenantId),
    enabled: tenantId !== "",
  });
  const bots = useQuery({
    queryKey: ["admin", "billing", "bots", tenantId],
    queryFn: () => listBillingBots(tenantId),
    enabled: tenantId !== "",
  });
  const invoices = useQuery({
    queryKey: ["admin", "billing", "invoices", page],
    queryFn: () => pageAdminInvoices(page, PAGE_SIZE),
  });
  const tenantPlan = useQuery({
    queryKey: ["admin", "billing", "tenant-plan", tenantId],
    queryFn: () => getTenantPlan(tenantId),
    enabled: tenantId !== "",
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "billing"] });
  };

  const create = useMutation({
    mutationFn: () => {
      const nums = {
        monthly_tokens: parseNonNegInt(planForm.monthly_tokens),
        monthly_calls: parseNonNegInt(planForm.monthly_calls),
        price_per_mtok: parseNonNegInt(planForm.price_per_mtok),
        price_per_call: parseNonNegInt(planForm.price_per_call),
      };
      const values = Object.values(nums);
      if (values.some((n) => n === null)) {
        throw new Error("数值须为非负整数(非法输入不会静默归零)");
      }
      // 显式展开守卫后字段(Record 展开会丢字面量类型,契约收紧后 tsc 报错——
      // 审查循环 F1 直证:手写 Record 曾让缺字段静默通过)
      const [monthly_tokens, monthly_calls, price_per_mtok, price_per_call] =
        values as [number, number, number, number];
      return createPlan({
        plan_key: planForm.plan_key.trim(),
        name: planForm.name.trim(),
        monthly_tokens,
        monthly_calls,
        price_per_mtok,
        price_per_call,
      });
    },
    onSuccess: () => {
      setPlanForm(EMPTY_PLAN_FORM);
      setPlanError("");
      invalidate();
    },
    onError: (e) => setPlanError(e instanceof Error ? e.message : "创建失败"),
  });

  const update = useMutation({
    mutationFn: () => {
      if (!editPlan) throw new Error("无编辑目标");
      const nums = {
        monthly_tokens: parseNonNegInt(editForm.monthly_tokens),
        monthly_calls: parseNonNegInt(editForm.monthly_calls),
        price_per_mtok: parseNonNegInt(editForm.price_per_mtok),
        price_per_call: parseNonNegInt(editForm.price_per_call),
      };
      const values = Object.values(nums);
      if (values.some((n) => n === null)) {
        throw new Error("数值须为非负整数");
      }
      const [monthly_tokens, monthly_calls, price_per_mtok, price_per_call] =
        values as [number, number, number, number];
      return updatePlan(editPlan.id, {
        name: editForm.name.trim(),
        monthly_tokens,
        monthly_calls,
        price_per_mtok,
        price_per_call,
      });
    },
    onSuccess: () => {
      setEditPlan(null);
      setEditError("");
      invalidate();
    },
    onError: (e) => setEditError(e instanceof Error ? e.message : "更新失败"),
  });

  const removePlan = useMutation({
    mutationFn: (id: string) => deletePlan(id),
    onSuccess: () => {
      setDeletePlanTarget(null);
      setDeletePlanError("");
      invalidate();
    },
    onError: (e) => setDeletePlanError(e instanceof Error ? e.message : "删除失败"),
  });

  const upsert = useMutation({
    mutationFn: () => {
      const limit = parseNonNegInt(policyForm.limit_value);
      const warn = parseNonNegInt(policyForm.warn_pct);
      if (limit === null || warn === null) {
        throw new Error("上限/预警须为非负整数");
      }
      return upsertPolicy({
        tenant_id: tenantId,
        dimension: policyForm.dimension,
        limit_value: limit,
        warn_pct: warn,
        scope: policyForm.scope,
        scope_ref: policyForm.scope === "BOT" ? policyForm.scope_ref || null : null,
      });
    },
    onSuccess: () => {
      setPolicyError("");
      invalidate();
    },
    onError: (e) => setPolicyError(e instanceof Error ? e.message : "保存失败"),
  });

  const removePolicy = useMutation({
    mutationFn: (id: string) => deletePolicy(id, tenantId),
    onSuccess: () => {
      setDeletePolicyId(null);
      invalidate();
    },
    // 审查循环 F4:失败零反馈=用户以为没点上反复重试
    onError: (e) => setPolicyError(e instanceof Error ? e.message : "删除策略失败"),
  });

  const bind = useMutation({
    mutationFn: (planId: string | null) => bindPlan(tenantId, planId),
    onSuccess: () => {
      setPolicyError("");
      invalidate();
    },
    onError: (e) => setPolicyError(e instanceof Error ? e.message : "绑定套餐失败"),
  });

  const voidMut = useMutation({
    mutationFn: (id: string) => voidInvoice(id),
    onSuccess: () => {
      setVoidTarget(null);
      setVoidError("");
      invalidate();
    },
    onError: (e) => setVoidError(e instanceof Error ? e.message : "作废失败"),
  });

  const totalPages = Math.max(1, Math.ceil((invoices.data?.total ?? 0) / PAGE_SIZE));

  return (
    <div className="space-y-5">
      <h1 className="text-lg font-semibold">计费</h1>

      <section className="space-y-3 rounded-lg border border-border p-4">
        <h2 className="text-sm font-medium">套餐</h2>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>标识</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>月 token</TableHead>
                <TableHead>月调用</TableHead>
                <TableHead>单价(厘/千t+厘/次)</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {plans.isError ? (
                <TableRow>
                  <TableCell colSpan={6}>
                    <QueryErrorAlert />
                  </TableCell>
                </TableRow>
              ) : (
                (plans.data ?? []).map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="font-mono text-xs">{p.plan_key}</TableCell>
                  <TableCell>{p.name}</TableCell>
                  <TableCell>{p.monthly_tokens.toLocaleString()}</TableCell>
                  <TableCell>{p.monthly_calls.toLocaleString()}</TableCell>
                  <TableCell>
                    {p.price_per_mtok} + {p.price_per_call}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`编辑套餐 ${p.plan_key}`}
                      onClick={() => {
                        setEditPlan(p);
                        setEditForm({
                          plan_key: p.plan_key,
                          name: p.name,
                          monthly_tokens: String(p.monthly_tokens),
                          monthly_calls: String(p.monthly_calls),
                          price_per_mtok: String(p.price_per_mtok),
                          price_per_call: String(p.price_per_call),
                        });
                        setEditError("");
                      }}
                    >
                      <Pencil aria-hidden />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`删除套餐 ${p.plan_key}`}
                      onClick={() => {
                        setDeletePlanTarget(p);
                        setDeletePlanError("");
                      }}
                    >
                      <Trash2 aria-hidden />
                    </Button>
                  </TableCell>
                </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
        <form
          className="grid grid-cols-2 gap-2 sm:grid-cols-6"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Input
            value={planForm.plan_key}
            onChange={(e) => setPlanForm({ ...planForm, plan_key: e.target.value })}
            placeholder="plan_key"
            aria-label="套餐标识"
            className="font-mono"
          />
          <Input
            value={planForm.name}
            onChange={(e) => setPlanForm({ ...planForm, name: e.target.value })}
            placeholder="名称"
            aria-label="套餐名称"
          />
          <Input
            value={planForm.monthly_tokens}
            onChange={(e) => setPlanForm({ ...planForm, monthly_tokens: e.target.value })}
            placeholder="月 token(0 不限)"
            aria-label="月 token"
          />
          <Input
            value={planForm.monthly_calls}
            onChange={(e) => setPlanForm({ ...planForm, monthly_calls: e.target.value })}
            placeholder="月调用(0 不限)"
            aria-label="月调用"
          />
          <Input
            value={planForm.price_per_mtok}
            onChange={(e) => setPlanForm({ ...planForm, price_per_mtok: e.target.value })}
            placeholder="厘/千 token"
            aria-label="千 token 单价"
          />
          <div className="flex gap-2">
            <Input
              value={planForm.price_per_call}
              onChange={(e) => setPlanForm({ ...planForm, price_per_call: e.target.value })}
              placeholder="厘/次"
              aria-label="次单价"
            />
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Plus aria-hidden />}
            </Button>
          </div>
        </form>
        {planError && (
          <p role="alert" className="text-sm text-destructive">
            {planError}
          </p>
        )}
      </section>

      <section className="space-y-3 rounded-lg border border-border p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium">租户配额策略</h2>
          <div className="flex items-center gap-2">
            <Select value={tenantId} onValueChange={setTenantId}>
              <SelectTrigger className="w-44" aria-label="选择租户">
                <SelectValue placeholder="选择租户" />
              </SelectTrigger>
              <SelectContent>
                {(tenants.data ?? []).map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.tenant_key}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {tenantId && (
              <Select
                value={tenantPlan.data?.id ?? undefined}
                onValueChange={(v) => bind.mutate(v === "_none" ? null : v)}
              >
                <SelectTrigger className="w-40" aria-label="绑套餐">
                  <SelectValue placeholder="绑套餐…" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="_none">解绑</SelectItem>
                  {(plans.data ?? []).map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        </div>
        {tenantId && (
          <>
            <div className="rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>范围</TableHead>
                    <TableHead>维度</TableHead>
                    <TableHead>上限(0=不限)</TableHead>
                    <TableHead>预警%</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {policies.isError ? (
                    <TableRow>
                      <TableCell colSpan={5}>
                        <QueryErrorAlert />
                      </TableCell>
                    </TableRow>
                  ) : (policies.data ?? []).length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="py-6 text-center text-muted-foreground">
                        无策略(不限量)
                      </TableCell>
                    </TableRow>
                  ) : (
                    (policies.data ?? []).map((p) => (
                      <TableRow key={p.id}>
                        <TableCell className="text-xs">
                          {p.scope === "BOT" ? (
                            <Badge variant="outline" className="font-normal">
                              BOT:{(bots.data ?? []).find((b) => b.id === p.scope_ref)?.bot_key ?? p.scope_ref?.slice(0, 8)}
                            </Badge>
                          ) : (
                            <Badge variant="secondary" className="font-normal">租户</Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary" className="font-normal">
                            {p.dimension}
                          </Badge>
                        </TableCell>
                        <TableCell>{p.limit_value.toLocaleString()}</TableCell>
                        <TableCell>{p.warn_pct}%</TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={`删除 ${p.dimension} 策略`}
                            onClick={() => setDeletePolicyId(p.id)}
                          >
                            <Trash2 aria-hidden />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
            <form
              className="flex items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                upsert.mutate();
              }}
            >
              <div className="space-y-1">
                <Label className="text-xs">范围</Label>
                <Select
                  value={policyForm.scope}
                  onValueChange={(v) => setPolicyForm({ ...policyForm, scope: v, scope_ref: "" })}
                >
                  <SelectTrigger className="w-28" aria-label="范围">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="TENANT">租户</SelectItem>
                    <SelectItem value="BOT">BOT</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {policyForm.scope === "BOT" && (
                <div className="space-y-1">
                  <Label className="text-xs">Bot</Label>
                  <Select
                    value={policyForm.scope_ref}
                    onValueChange={(v) => setPolicyForm({ ...policyForm, scope_ref: v })}
                  >
                    <SelectTrigger className="w-40" aria-label="选择 Bot">
                      <SelectValue placeholder="选择 Bot" />
                    </SelectTrigger>
                    <SelectContent>
                      {(bots.data ?? []).map((b) => (
                        <SelectItem key={b.id} value={b.id}>
                          {b.bot_key}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <Select
                value={policyForm.dimension}
                onValueChange={(v) => setPolicyForm({ ...policyForm, dimension: v })}
              >
                <SelectTrigger className="w-32" aria-label="维度">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="TOKENS">TOKENS</SelectItem>
                  <SelectItem value="CALLS">CALLS</SelectItem>
                  <SelectItem value="COST">COST</SelectItem>
                </SelectContent>
              </Select>
              <div className="space-y-1">
                <Label className="text-xs">上限</Label>
                <Input
                  value={policyForm.limit_value}
                  onChange={(e) => setPolicyForm({ ...policyForm, limit_value: e.target.value })}
                  aria-label="上限"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">预警%</Label>
                <Input
                  value={policyForm.warn_pct}
                  onChange={(e) => setPolicyForm({ ...policyForm, warn_pct: e.target.value })}
                  className="w-20"
                  aria-label="预警百分比"
                />
              </div>
              <Button
                type="submit"
                disabled={upsert.isPending || (policyForm.scope === "BOT" && policyForm.scope_ref === "")}
              >
                {upsert.isPending && <Loader2 className="animate-spin" aria-hidden />}
                保存
              </Button>
            </form>
            {policyForm.dimension === "COST" && tenantPlan.data == null && (
              <p className="text-xs text-muted-foreground" role="note">
                提示:该租户未绑定套餐,COST 维成本估算单价为 0,策略将达不成限量
                (COST 维以套餐单价为前提,035 口径注记)。
              </p>
            )}
            {policyError && (
              <p role="alert" className="text-sm text-destructive">
                {policyError}
              </p>
            )}
          </>
        )}
      </section>

      <section className="space-y-2 rounded-lg border border-border p-4">
        <h2 className="text-sm font-medium">全部账单</h2>
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>租户</TableHead>
                <TableHead>周期</TableHead>
                <TableHead>金额(厘)</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>支付单</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoices.isError ? (
                <TableRow>
                  <TableCell colSpan={6}>
                    <QueryErrorAlert />
                  </TableCell>
                </TableRow>
              ) : (invoices.data?.items ?? []).length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-6 text-center text-muted-foreground">
                    暂无账单
                  </TableCell>
                </TableRow>
              ) : (
                (invoices.data?.items ?? []).map((inv) => (
                  <TableRow key={inv.id}>
                    <TableCell className="font-mono text-xs">{inv.tenant_key}</TableCell>
                    <TableCell className="font-mono text-xs">{inv.period}</TableCell>
                    <TableCell>{inv.amount.toLocaleString()}</TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="font-normal">
                        {inv.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {inv.payment_status ?? "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      {inv.status === "PENDING" && (
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => {
                            setVoidTarget(inv.id);
                            setVoidError("");
                          }}
                        >
                          作废
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            共 {invoices.data?.total ?? 0} 条 · 第 {page}/{totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="xs"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="xs"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </section>

      {/* 套餐编辑对话框 */}
      <Dialog open={editPlan !== null} onOpenChange={(o) => !o && setEditPlan(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>编辑套餐 {editPlan?.plan_key}</DialogTitle>
          </DialogHeader>
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              update.mutate();
            }}
          >
            <Label className="text-xs">名称</Label>
            <Input
              value={editForm.name}
              onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              aria-label="套餐名称"
            />
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label className="text-xs">月 token(0 不限)</Label>
                <Input
                  value={editForm.monthly_tokens}
                  onChange={(e) => setEditForm({ ...editForm, monthly_tokens: e.target.value })}
                  aria-label="月 token"
                />
              </div>
              <div>
                <Label className="text-xs">月调用(0 不限)</Label>
                <Input
                  value={editForm.monthly_calls}
                  onChange={(e) => setEditForm({ ...editForm, monthly_calls: e.target.value })}
                  aria-label="月调用"
                />
              </div>
              <div>
                <Label className="text-xs">厘/千 token</Label>
                <Input
                  value={editForm.price_per_mtok}
                  onChange={(e) => setEditForm({ ...editForm, price_per_mtok: e.target.value })}
                  aria-label="千 token 单价"
                />
              </div>
              <div>
                <Label className="text-xs">厘/次</Label>
                <Input
                  value={editForm.price_per_call}
                  onChange={(e) => setEditForm({ ...editForm, price_per_call: e.target.value })}
                  aria-label="次单价"
                />
              </div>
            </div>
            {editError && (
              <p role="alert" className="text-sm text-destructive">
                {editError}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              改价不回溯既有账单(账单明细为出账时快照)。
            </p>
            <Button type="submit" disabled={update.isPending}>
              {update.isPending && <Loader2 className="animate-spin" aria-hidden />}
              保存
            </Button>
          </form>
        </DialogContent>
      </Dialog>

      {/* 套餐删除确认(035 挂账⑦:013 模式——busy 期禁关) */}
      <AlertDialog
        open={deletePlanTarget !== null}
        onOpenChange={(o) => {
          if (!o && !removePlan.isPending) setDeletePlanTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除套餐 {deletePlanTarget?.plan_key}?</AlertDialogTitle>
            <AlertDialogDescription>
              绑定中的套餐不可删除(409 PLAN_IN_USE);删除后新租户不可再绑定,既有账单不受影响。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deletePlanError && (
            <p role="alert" className="text-sm text-destructive">
              {deletePlanError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removePlan.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={removePlan.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (deletePlanTarget) removePlan.mutate(deletePlanTarget.id);
              }}
            >
              {removePlan.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 策略删除确认(035 挂账⑦) */}
      <AlertDialog
        open={deletePolicyId !== null}
        onOpenChange={(o) => {
          if (!o && !removePolicy.isPending) setDeletePolicyId(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除该配额策略?</AlertDialogTitle>
            <AlertDialogDescription>
              删除即解除该维度限量防线(操作落审计)。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removePolicy.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={removePolicy.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (deletePolicyId) removePolicy.mutate(deletePolicyId);
              }}
            >
              {removePolicy.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 账单作废确认 */}
      <AlertDialog
        open={voidTarget !== null}
        onOpenChange={(o) => {
          if (!o && !voidMut.isPending) setVoidTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>作废该账单?</AlertDialogTitle>
            <AlertDialogDescription>
              作废后不可再支付(在途支付单将连带取消);已支付账单不可作废。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {voidError && (
            <p role="alert" className="text-sm text-destructive">
              {voidError}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={voidMut.isPending}>再想想</AlertDialogCancel>
            <AlertDialogAction
              disabled={voidMut.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (voidTarget) voidMut.mutate(voidTarget);
              }}
            >
              {voidMut.isPending && <Loader2 className="animate-spin" aria-hidden />}
              确认作废
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
