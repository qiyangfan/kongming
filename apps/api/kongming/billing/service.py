"""账单与配额用例,按 Asia/Shanghai 时区处理月度账单;支付核销统一经 reconcile_payment。"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from kongming.billing.models import (
    QuotaVerdict,
    can_create_payment,
    check_quota,
    estimate_cost,
)
from kongming.billing.ports import BillingRepo, PaymentChannel

_TZ = dt.timezone(dt.timedelta(hours=8))


class BillingValidationError(Exception):
    """400(套餐/策略形态/金额不符)。"""


class BillingNotFound(Exception):
    """404(账单/支付单/套餐不存在或跨租户同形)。"""


class PaymentConflict(Exception):
    """409(终态不可变/已支付不可再付/绑定中套餐不可删)。"""


class PaymentBadSignature(Exception):
    """401(回调验签失败)。"""


class PaymentChannelClosed(Exception):
    """404(mock 通道经环境开关关闭/通道未注册)。"""


class BillingOperLogger(Protocol):
    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...


def _dim_limit(
    policies: dict[str, dict[str, Any]],
    plan: dict[str, Any] | None,
    dimension: str,
    plan_key: str,
) -> int:
    """租户级单维限额:策略优先,套餐字段兜底,都没有按 0(不限)。"""
    if dimension in policies:
        return int(policies[dimension]["limit_value"])
    return int(plan[plan_key]) if plan else 0


def _warn_pct(policies: dict[str, dict[str, Any]], dimension: str) -> int:
    """按维取策略 warn_pct(缺省 80;多策略取 max 会吞单策略线)。"""
    return int(policies[dimension]["warn_pct"]) if dimension in policies else 80


def _tenant_warn_lines(
    fixed: list[tuple[str, int, int, QuotaVerdict]],
    cost: tuple[str, QuotaVerdict | None, int],
) -> list[str]:
    """租户级预警头:TOKENS/CALLS 按 used/limit,COST 按判定用量;限额 0(不限)与未达线不记。"""
    lines = [f"{dim} {used}/{limit}" for dim, used, limit, v in fixed if v.warn and limit > 0]
    cost_dim, cost_v, cost_limit = cost
    if cost_v is not None and cost_v.warn and cost_limit > 0:
        lines.append(f"{cost_dim} {cost_v.used}/{cost_limit}")
    return lines


def _current_month() -> str:
    return dt.datetime.now(_TZ).strftime("%Y-%m")


def _prev_month() -> str:
    now = dt.datetime.now(_TZ)
    first = now.replace(day=1)
    prev = first - dt.timedelta(days=1)
    return prev.strftime("%Y-%m")


class _QuotaDenied(Exception):
    """单路配额超限(scope 为拒绝范围,如 BOT:TOKENS/TOKENS;审计已先行落)。"""

    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


@dataclass
class _QuotaScopes:
    """各路命中策略(按 dimension 索引;team_pols 避开外部 team 变量)。"""

    tenant: dict[str, dict[str, Any]]
    bot: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    user: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    team_pols: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    owner: str | None = None
    team: str | None = None


@dataclass(frozen=True)
class _ScopeCheck:
    """BOT/USER/TEAM 单路判定输入;scope 即拒绝范围/预警串前缀。"""

    scope: str
    ref_key: str | None
    ref: str | None
    policies: dict[str, dict[str, Any]]


def _by_dimension(
    policies: list[dict[str, Any]], scope: str, ref: str | None
) -> dict[str, dict[str, Any]]:
    """按 scope 与 scope_ref 过滤并索引到 dimension(ref=None 只匹配无引用行)。"""
    return {
        p["dimension"]: p
        for p in policies
        if p.get("scope") == scope and str(p.get("scope_ref")) == str(ref)
    }


def _scope_cost(tokens: int, plan: dict[str, Any] | None) -> int:
    """BOT/USER/TEAM 路成本:只按 tokens 计价(该三维无调用次数口径)。"""
    return estimate_cost(
        tokens=tokens,
        calls=0,
        price_per_mtok=int(plan["price_per_mtok"]) if plan else 0,
        price_per_call=0,
    )


def _scope_warn_lines(
    check: _ScopeCheck,
    tokens: int,
    vt: QuotaVerdict | None,
    vcost: QuotaVerdict | None,
) -> list[str]:
    """BOT/USER/TEAM 预警串:TOKENS 达线且限额>0;COST 达线(分母取策略限额)。"""
    lines: list[str] = []
    p = check.policies.get("TOKENS")
    if p is not None and int(p["limit_value"]) > 0 and vt is not None and vt.warn:
        lines.append(f"{check.scope}:TOKENS {tokens}/{int(p['limit_value'])}")
    pc = check.policies.get("COST")
    if vcost is not None and vcost.warn and pc is not None:
        lines.append(f"{check.scope}:COST {vcost.used}/{int(pc['limit_value'])}")
    return lines


class BillingService:
    def __init__(
        self,
        repo: BillingRepo,
        oper: BillingOperLogger | None = None,
        *,
        channels: dict[str, PaymentChannel] | None = None,
        payment_ttl_minutes: int = 30,
    ) -> None:
        self._repo = repo
        self._oper = oper
        self._channels = channels or {}
        self._ttl = payment_ttl_minutes

    async def _audit(self, action: str, target: str, detail: dict[str, object]) -> None:
        if self._oper is not None:
            await self._oper.log(action=action, target=target, detail=detail)

    # ── 配额判定(网关切面) ──

    async def check_gateway_quota(
        self, *, tenant_id: str, bot_id: str | None = None
    ) -> tuple[bool, str, str]:
        """依次独立检查 TENANT/BOT/USER/TEAM 四路配额,任一超限即拒绝并记审计;
        返回(是否允许, 拒绝范围, 告警头)。无策略且无套餐时放行;
        USER 按 Bot 创建者归属,TEAM 按 Bot 当前团队归属统计。"""
        all_p = await self._repo.list_policies(tenant_id=tenant_id)
        policies = {p["dimension"]: p for p in all_p if p.get("scope") == "TENANT"}
        plan = await self._repo.get_tenant_plan(tenant_id=tenant_id)
        month = _current_month()
        scopes = await self._resolve_quota_scopes(tenant_id=tenant_id, bot_id=bot_id, all_p=all_p)
        # 无任何配置=零判定(spec「未配置」)
        if (
            not policies
            and not scopes.bot
            and not scopes.user
            and not scopes.team_pols
            and plan is None
        ):
            return True, "", ""
        # 路 1:租户级(判 TOKENS→CALLS→COST,文案无前缀)
        try:
            warns = await self._check_tenant_scope(
                policies, tenant_id=tenant_id, month=month, plan=plan
            )
        except _QuotaDenied as denied:
            return False, denied.scope, ""
        # 路 2-4:BOT/USER/TEAM(TOKENS/COST 两维,无套餐兜底=0 不限)
        for check in (
            _ScopeCheck("BOT", "bot_id", bot_id, scopes.bot),
            _ScopeCheck("USER", "user_id", scopes.owner, scopes.user),
            _ScopeCheck("TEAM", "team_id", scopes.team, scopes.team_pols),
        ):
            if not check.policies:
                continue
            try:
                await self._check_bot_side(
                    check, tenant_id=tenant_id, month=month, plan=plan, warns=warns
                )
            except _QuotaDenied as denied:
                return False, denied.scope, ""
        return True, "", "; ".join(warns)

    async def _resolve_quota_scopes(
        self, *, tenant_id: str, bot_id: str | None, all_p: list[dict[str, Any]]
    ) -> _QuotaScopes:
        """命中收集:各路策略互斥命中(按 dimension 索引)。"""
        scopes = _QuotaScopes(tenant=_by_dimension(all_p, "TENANT", None))
        if not bot_id:
            return scopes
        scopes.bot = _by_dimension(all_p, "BOT", bot_id)
        info = await self._repo.bot_scope_info(bot_id=bot_id, tenant_id=tenant_id)
        if info is not None:
            scopes.owner = str(info["created_by"]) if info.get("created_by") else None
            scopes.team = str(info["team_id"]) if info.get("team_id") else None
        if scopes.owner:
            scopes.user = _by_dimension(all_p, "USER", scopes.owner)
        if scopes.team:
            scopes.team_pols = _by_dimension(all_p, "TEAM", scopes.team)
        return scopes

    async def _month_tokens(self, check: _ScopeCheck, *, tenant_id: str, month: str) -> int:
        """按路取本月 tokens 用量(引用缺失按空串口径,由仓储返回零用量)。"""
        ref = check.ref or ""
        if check.scope == "BOT":
            return await self._repo.month_bot_token_usage(
                tenant_id=tenant_id, bot_id=ref, month=month
            )
        if check.scope == "USER":
            return await self._repo.month_user_token_usage(
                tenant_id=tenant_id, user_id=ref, month=month
            )
        return await self._repo.month_team_token_usage(
            tenant_id=tenant_id, team_id=ref, month=month
        )

    async def _check_tenant_scope(
        self,
        policies: dict[str, dict[str, Any]],
        *,
        tenant_id: str,
        month: str,
        plan: dict[str, Any] | None,
    ) -> list[str]:
        """路 1:租户级三维判定(限额策略优先、套餐兜底);超限抛 _QuotaDenied 并先行记审计。"""
        tokens = await self._repo.month_token_usage(tenant_id=tenant_id, month=month)
        calls = await self._repo.month_call_count(tenant_id=tenant_id, month=month)
        # 限额解析:策略优先,套餐兜底(0=不限)
        token_limit = _dim_limit(policies, plan, "TOKENS", "monthly_tokens")
        call_limit = _dim_limit(policies, plan, "CALLS", "monthly_calls")
        vt = await self._enforce_quota(tokens, token_limit, "TOKENS", policies, tenant_id=tenant_id)
        vc = await self._enforce_quota(calls, call_limit, "CALLS", policies, tenant_id=tenant_id)
        # 预警头:任一维达线即记录
        return await self._tenant_cost_warn(
            vt=vt,
            vc=vc,
            tokens=tokens,
            calls=calls,
            token_limit=token_limit,
            call_limit=call_limit,
            policies=policies,
            plan=plan,
            tenant_id=tenant_id,
        )

    async def _tenant_cost_warn(
        self,
        *,
        vt: QuotaVerdict,
        vc: QuotaVerdict,
        tokens: int,
        calls: int,
        token_limit: int,
        call_limit: int,
        policies: dict[str, dict[str, Any]],
        plan: dict[str, Any] | None,
        tenant_id: str,
    ) -> list[str]:
        """租户级 COST 维判定(限额 0 不限)并汇总三维预警头。"""
        cost_limit = int(policies["COST"]["limit_value"]) if "COST" in policies else 0
        vcost = None
        if cost_limit > 0:
            pm = int(plan["price_per_mtok"]) if plan else 0
            pc = int(plan["price_per_call"]) if plan else 0
            cost = estimate_cost(tokens=tokens, calls=calls, price_per_mtok=pm, price_per_call=pc)
            vcost = await self._enforce_quota(
                cost, cost_limit, "COST", policies, tenant_id=tenant_id
            )
        return _tenant_warn_lines(
            [("TOKENS", tokens, token_limit, vt), ("CALLS", calls, call_limit, vc)],
            ("COST", vcost, cost_limit),
        )

    async def _enforce_quota(
        self,
        used: int,
        limit: int,
        dimension: Literal["TOKENS", "CALLS", "COST"],
        policies: dict[str, dict[str, Any]],
        *,
        tenant_id: str,
    ) -> QuotaVerdict:
        """租户级单维判定:按该维策略的 warn_pct(缺省 80)判,超限先记审计再抛 _QuotaDenied。"""
        v = check_quota(
            used=used, limit=limit, warn_pct=_warn_pct(policies, dimension), dimension=dimension
        )
        if not v.allowed:
            await self._audit(
                "billing.quota_reject",
                tenant_id,
                {"dimension": dimension, "used": used, "limit": limit},
            )
            raise _QuotaDenied(dimension)
        return v

    async def _check_bot_side(
        self,
        check: _ScopeCheck,
        *,
        tenant_id: str,
        month: str,
        plan: dict[str, Any] | None,
        warns: list[str],
    ) -> None:
        """BOT/USER/TEAM 单路:取本月 tokens → 判 TOKENS/COST(超限抛)→ 收两维预警。"""
        tokens = await self._month_tokens(check, tenant_id=tenant_id, month=month)
        vt = await self._pass_dim(check, "TOKENS", tokens, tenant_id=tenant_id)
        vcost = await self._pass_dim(check, "COST", _scope_cost(tokens, plan), tenant_id=tenant_id)
        warns.extend(_scope_warn_lines(check, tokens, vt, vcost))

    async def _pass_dim(
        self,
        check: _ScopeCheck,
        dimension: Literal["TOKENS", "CALLS", "COST"],
        used: int,
        *,
        tenant_id: str,
    ) -> QuotaVerdict | None:
        """单维判定;超限记审计后抛 _QuotaDenied;COST 未配置/限额 0 返回 None(不限)。"""
        p = check.policies.get(dimension)
        if p is None or (dimension == "COST" and int(p["limit_value"]) <= 0):
            return None
        limit = int(p["limit_value"])
        v = check_quota(used=used, limit=limit, warn_pct=int(p["warn_pct"]), dimension=dimension)
        if not v.allowed:
            await self._audit(
                "billing.quota_reject",
                tenant_id,
                {
                    "scope": check.scope,
                    check.ref_key or "scope": check.ref,
                    "dimension": dimension,
                    "used": used,
                    "limit": limit,
                },
            )
            raise _QuotaDenied(f"{check.scope}:{dimension}")
        return v

    # ── 配置 ──

    async def upsert_policy(
        self,
        *,
        tenant_id: str,
        dimension: str,
        limit_value: int,
        warn_pct: int,
        scope: str = "TENANT",
        scope_ref: str | None = None,
    ) -> None:
        if dimension not in ("TOKENS", "CALLS", "COST"):
            raise BillingValidationError("dimension 须为 TOKENS/CALLS/COST")
        if limit_value < 0:
            raise BillingValidationError("limit_value 须 ≥0(0=不限)")
        if not 1 <= warn_pct <= 100:
            raise BillingValidationError("warn_pct 须 1-100")
        # scope 校验(BOT=租户内 Bot;USER=租户内用户;TEAM=租户内
        # 团队;CALLS 维无资源/用户/团队级归属口径,仅 TENANT 立约)
        if scope not in ("TENANT", "BOT", "USER", "TEAM"):
            raise BillingValidationError("scope 须为 TENANT/BOT/USER/TEAM")
        if scope == "TENANT":
            scope_ref = None  # 强制空(CHECK 互斥;防脏数据)
        else:
            await self._ensure_scope_ref(
                scope=scope, dimension=dimension, scope_ref=scope_ref, tenant_id=tenant_id
            )
        await self._repo.upsert_policy(
            tenant_id=tenant_id,
            dimension=dimension,
            limit_value=limit_value,
            warn_pct=warn_pct,
            scope=scope,
            scope_ref=scope_ref,
        )
        await self._audit(
            "billing.policy.upsert",
            tenant_id,
            {"dimension": dimension, "limit": limit_value, "scope": scope, "scope_ref": scope_ref},
        )

    async def _ensure_scope_ref(
        self, *, scope: str, dimension: str, scope_ref: str | None, tenant_id: str
    ) -> None:
        """非 TENANT 级:拒绝 CALLS 维与空引用,并校验引用资源在本租户存在(否则 404)。"""
        if dimension == "CALLS":
            raise BillingValidationError("CALLS 维度仅支持 TENANT 级配额")
        if not scope_ref:
            raise BillingValidationError(f"{scope} 级须指定 scope_ref")
        if scope == "BOT":
            exists = await self._repo.bot_exists_in_tenant(bot_id=scope_ref, tenant_id=tenant_id)
        elif scope == "USER":
            exists = await self._repo.user_exists_in_tenant(user_id=scope_ref, tenant_id=tenant_id)
        else:  # TEAM
            exists = await self._repo.team_exists_in_tenant(team_id=scope_ref, tenant_id=tenant_id)
        if not exists:
            raise BillingNotFound(f"{scope}_NOT_FOUND")

    async def create_plan(self, **kw: Any) -> str:
        if not str(kw.get("name", "")).strip():
            raise BillingValidationError("套餐名称非空")
        for k in ("monthly_tokens", "monthly_calls", "price_per_mtok", "price_per_call"):
            if int(kw.get(k, 0)) < 0:
                raise BillingValidationError(f"{k} 须 ≥0")
        rid = await self._repo.create_plan(**kw)
        await self._audit("billing.plan.create", kw["plan_key"], {"name": kw["name"]})
        return rid

    # ── 出账(月首幂等) ──

    async def generate_monthly_invoices(self) -> int:
        """为上月未出账租户各落一条账单;返回新出账条数(幂等)。"""
        period = _prev_month()
        tenants = await self._repo.list_tenants_without_invoice(period=period)
        created = 0
        for tenant in tenants:
            tid = str(tenant["id"])
            by_model = await self._repo.month_usage_by_model(tenant_id=tid, month=period)
            if not by_model:
                continue  # 零用量不出账
            tokens = await self._repo.month_token_usage(tenant_id=tid, month=period)
            calls = await self._repo.month_call_count(tenant_id=tid, month=period)
            plan = await self._repo.get_tenant_plan(tenant_id=tid)
            pm = int(plan["price_per_mtok"]) if plan else 0
            pc = int(plan["price_per_call"]) if plan else 0
            amount = estimate_cost(tokens=tokens, calls=calls, price_per_mtok=pm, price_per_call=pc)
            rid = await self._repo.insert_invoice(
                tenant_id=tid,
                period=period,
                usage_json={
                    "by_model": [
                        {
                            "model": str(m["model"]),
                            "prompt": int(m["prompt"]),
                            "completion": int(m["completion"]),
                            "calls": int(m["calls"]),
                        }
                        for m in by_model
                    ],
                    "total_tokens": tokens,
                    "total_calls": calls,
                    "price_per_mtok": pm,
                    "price_per_call": pc,
                },
                amount=amount,
            )
            if rid is not None:
                created += 1
                await self._audit(
                    "billing.invoice.create", tid, {"period": period, "amount": amount}
                )
        await self._repo.commit()
        return created

    # ── 门户聚合 ──

    async def portal_usage(self, *, tenant_id: str) -> dict[str, Any]:
        month = _current_month()
        tokens = await self._repo.month_token_usage(tenant_id=tenant_id, month=month)
        calls = await self._repo.month_call_count(tenant_id=tenant_id, month=month)
        plan = await self._repo.get_tenant_plan(tenant_id=tenant_id)
        policies = await self._repo.list_policies(tenant_id=tenant_id)
        pm = int(plan["price_per_mtok"]) if plan else 0
        pc = int(plan["price_per_call"]) if plan else 0
        cost = estimate_cost(tokens=tokens, calls=calls, price_per_mtok=pm, price_per_call=pc)
        return {
            "month": month,
            "tokens": tokens,
            "calls": calls,
            "cost": cost,
            "policies": policies,
            "plan": plan,
        }

    # ── 套餐更新/删除 ──

    async def update_plan(self, *, plan_id: str, **kw: Any) -> None:
        old = await self._repo.get_plan(plan_id=plan_id)
        if old is None:
            raise BillingNotFound("PLAN_NOT_FOUND")
        if not str(kw.get("name", "")).strip():
            raise BillingValidationError("套餐名称非空")
        for k in ("monthly_tokens", "monthly_calls", "price_per_mtok", "price_per_call"):
            if int(kw.get(k, 0)) < 0:
                raise BillingValidationError(f"{k} 须 ≥0")
        await self._repo.update_plan(plan_id=plan_id, **kw)
        await self._audit(
            "billing.plan.update",
            str(old["plan_key"]),
            {
                "before": {
                    "name": old["name"],
                    "monthly_tokens": int(old["monthly_tokens"]),
                    "monthly_calls": int(old["monthly_calls"]),
                    "price_per_mtok": int(old["price_per_mtok"]),
                    "price_per_call": int(old["price_per_call"]),
                },
                "after": {
                    "name": kw["name"],
                    "monthly_tokens": kw["monthly_tokens"],
                    "monthly_calls": kw["monthly_calls"],
                    "price_per_mtok": kw["price_per_mtok"],
                    "price_per_call": kw["price_per_call"],
                },
            },
        )

    async def delete_plan(self, *, plan_id: str) -> None:
        old = await self._repo.get_plan(plan_id=plan_id)
        if old is None:
            raise BillingNotFound("PLAN_NOT_FOUND")
        if await self._repo.count_plan_bindings(plan_id=plan_id) > 0:
            raise PaymentConflict("PLAN_IN_USE")
        await self._repo.delete_plan(plan_id=plan_id)
        await self._audit("billing.plan.delete", str(old["plan_key"]), {"name": old["name"]})

    # ── 支付流程 ──

    def _channel(self, channel: str) -> PaymentChannel:
        ch = self._channels.get(channel)
        if ch is None:
            raise PaymentChannelClosed("CHANNEL_UNAVAILABLE")
        return ch

    async def create_payment(self, *, invoice_id: str, tenant_id: str) -> dict[str, Any]:
        """门户发起支付:每账单一在途单(旧单先惰性过期再清场)。

        条件插入原子兜底并发竞态:插入失败时重读分流——账单已非 PENDING
        =409;仍有在途单=双击幂等返回现有单(不新建不重复审计)。
        """
        ch = self._channel("MOCK")
        inv = await self._repo.get_invoice(invoice_id=invoice_id, tenant_id=tenant_id)
        if inv is None:
            raise BillingNotFound("NOT_FOUND")
        if not can_create_payment(str(inv["status"])):
            raise PaymentConflict(f"INVOICE_{inv['status']}")
        # 旧在途单:过期的惰性置 EXPIRED(审计),剩余置 CANCELLED(重付语义)
        old_open = await self._repo.latest_payment_for_invoice(invoice_id=invoice_id)
        if old_open is not None and old_open["status"] == "CREATED":
            if await self._repo.expire_payment_if_overdue(payment_id=str(old_open["id"])):
                await self._audit(
                    "payment.expired", str(old_open["id"]), {"invoice_id": invoice_id}
                )
        cancelled = await self._repo.cancel_open_payments(invoice_id=invoice_id)
        if cancelled:
            await self._audit(
                "payment.cancel", invoice_id, {"reason": "reissue", "count": cancelled}
            )
        expires = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=self._ttl)
        rid = await self._repo.insert_payment_if_pending(
            invoice_id=invoice_id,
            tenant_id=tenant_id,
            channel=ch.key,
            amount=int(inv["amount"]),
            expires_at=expires,
        )
        if rid is None:
            # 并发分流:账单已非 PENDING(被支付/作废)→409;仍有在途单
            # (并发双击本方取消未覆盖)→幂等返回现有单
            cur = await self._repo.get_invoice(invoice_id=invoice_id, tenant_id=tenant_id)
            if cur is None or not can_create_payment(str(cur["status"])):
                raise PaymentConflict(f"INVOICE_{cur['status'] if cur else 'NOT_FOUND'}")
            open_pay = await self._repo.latest_payment_for_invoice(invoice_id=invoice_id)
            if open_pay is not None and open_pay["status"] == "CREATED":
                return dict(open_pay)
            raise PaymentConflict("INVOICE_RACE")  # 理论不可达(防御)
        await self._audit(
            "payment.create",
            rid,
            {"invoice_id": invoice_id, "amount": int(inv["amount"]), "channel": ch.key},
        )
        row = await self._repo.get_payment(payment_id=rid)
        if row is None:  # 同事务刚插入,理论不可达;显式异常代 assert(S101)
            raise BillingNotFound("PAYMENT_VANISHED")
        return dict(row)

    async def get_portal_payment(self, *, payment_id: str, tenant_id: str) -> dict[str, Any]:
        """门户查询(归属复校+惰性过期)。"""
        pay = await self._repo.get_payment_for_tenant(payment_id=payment_id, tenant_id=tenant_id)
        if pay is None:
            raise BillingNotFound("NOT_FOUND")
        if pay["status"] == "CREATED" and await self._repo.expire_payment_if_overdue(
            payment_id=payment_id
        ):
            await self._audit("payment.expired", payment_id, {"invoice_id": str(pay["invoice_id"])})
            pay = await self._repo.get_payment(payment_id=payment_id)
            if pay is None:  # 刚置 EXPIRED 的行,理论不可达(S101)
                raise BillingNotFound("PAYMENT_VANISHED")
        return dict(pay)

    async def cancel_payment(self, *, payment_id: str, tenant_id: str) -> None:
        pay = await self.get_portal_payment(payment_id=payment_id, tenant_id=tenant_id)
        if not await self._repo.cas_cancel_payment(payment_id=payment_id):
            raise PaymentConflict(f"PAYMENT_{pay['status']}")
        await self._audit(
            "payment.cancel",
            payment_id,
            {"invoice_id": str(pay["invoice_id"]), "reason": "user"},
        )

    async def reconcile_payment(
        self,
        *,
        channel: str,
        payment_id: str,
        trade_no: str,
        result: str,
        amount: int,
        sign: str,
    ) -> dict[str, Any]:
        """唯一核销点:mock-simulate 与 callback 两入口汇聚于此。

        验签→金额复核→惰性过期→CAS→invoice 同事务联动→审计;
        重复回调/终态单据收敛为幂等响应(通道重试是常态,报错诱发无限重试)。
        """
        ch = self._channel(channel)
        pay = await self._repo.get_payment(payment_id=payment_id)
        if pay is None:
            raise BillingNotFound("NOT_FOUND")
        if not ch.verify(
            payment_id=payment_id,
            trade_no=trade_no,
            result=result,
            amount=amount,
            sign=sign,
        ):
            await self._audit("payment.bad_signature", payment_id, {"channel": channel})
            raise PaymentBadSignature("BAD_SIGNATURE")
        if int(amount) != int(pay["amount"]):
            await self._audit(
                "payment.amount_mismatch",
                payment_id,
                {"expected": int(pay["amount"]), "got": int(amount)},
            )
            raise BillingValidationError("AMOUNT_MISMATCH")
        # 惰性过期:过期单置 EXPIRED 后核销按幂等收敛不再生效
        if pay["status"] == "CREATED" and await self._repo.expire_payment_if_overdue(
            payment_id=payment_id
        ):
            await self._audit("payment.expired", payment_id, {"invoice_id": str(pay["invoice_id"])})
            pay["status"] = "EXPIRED"
        if result == "FAILURE":
            if pay["status"] == "CREATED":
                await self._audit("payment.failed", payment_id, {"trade_no": trade_no})
            return {"idempotent": pay["status"] != "CREATED", "status": str(pay["status"])}
        ok = await self._repo.cas_pay(payment_id=payment_id, trade_no=trade_no)
        if not ok:
            # 幂等败者重读——并发双回调败者拿到的快照可能已陈旧
            fresh = await self._repo.get_payment(payment_id=payment_id)
            return {
                "idempotent": True,
                "status": str(fresh["status"]) if fresh else str(pay["status"]),
            }
        linked = await self._repo.mark_invoice_paid(invoice_id=str(pay["invoice_id"]))
        if not linked:
            # 单已 PAID 而账单非 PENDING(极端并发:作废/重复支付窗)——
            # 钱已收,静默当正常不可取,落异常审计给运维信号
            await self._audit(
                "payment.invoice_not_pending",
                payment_id,
                {"invoice_id": str(pay["invoice_id"]), "trade_no": trade_no},
            )
        await self._audit(
            "payment.paid",
            payment_id,
            {
                "invoice_id": str(pay["invoice_id"]),
                "trade_no": trade_no,
                "amount": int(pay["amount"]),
            },
        )
        return {"idempotent": False, "status": "PAID"}

    async def mock_simulate(
        self, *, payment_id: str, tenant_id: str, result: str
    ) -> dict[str, Any]:
        """mock 通道「支付动作」入口(收银台按钮):服务端生成签名后走
        统一核销路径——前端不持密钥、不可直连改单。"""
        ch = self._channel("MOCK")
        pay = await self.get_portal_payment(payment_id=payment_id, tenant_id=tenant_id)
        trade_no = ch.new_trade_no()
        sign = ch.sign_payload(
            payment_id=payment_id,
            trade_no=trade_no,
            result=result,
            amount=int(pay["amount"]),
        )
        return await self.reconcile_payment(
            channel=ch.key,
            payment_id=payment_id,
            trade_no=trade_no,
            result=result,
            amount=int(pay["amount"]),
            sign=sign,
        )

    async def void_invoice(self, *, invoice_id: str) -> None:
        inv = await self._repo.get_invoice_admin(invoice_id=invoice_id)
        if inv is None:
            raise BillingNotFound("NOT_FOUND")
        if str(inv["status"]) == "PAID":
            raise PaymentConflict("INVOICE_PAID")
        cancelled = await self._repo.cancel_open_payments(invoice_id=invoice_id)
        if not await self._repo.cas_void_invoice(invoice_id=invoice_id):
            raise PaymentConflict(f"INVOICE_{inv['status']}")
        await self._audit(
            "billing.invoice.void",
            invoice_id,
            {
                "period": str(inv["period"]),
                "amount": int(inv["amount"]),
                "open_payments_cancelled": cancelled,
            },
        )

    async def expire_overdue_payments(self) -> int:
        """lifespan 定时批量兜底:过期超时未支付的 CREATED 单。"""
        rows = await self._repo.expire_overdue_payments()
        for r in rows:
            await self._audit(
                "payment.expired",
                str(r["id"]),
                {"invoice_id": str(r["invoice_id"]), "via": "sweeper"},
            )
        return len(rows)
