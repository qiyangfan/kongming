"""billing 用例编排(034):配额判定(网关切面)+ 出账(月首幂等)。
零框架(宪法 Article II);时区固定 Asia/Shanghai(与 031 同口径)。

035:支付闭环——reconcile_payment 为**唯一核销点**(mock-simulate 与
callback 两入口汇聚,design D1);状态机转换一律经仓储 CAS,纯函数
预判(models)只做文案与提前拒绝,不作并发依据。
"""

import datetime as dt
from typing import Any, Protocol

from kongming.billing.models import (
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


def _current_month() -> str:
    return dt.datetime.now(_TZ).strftime("%Y-%m")


def _prev_month() -> str:
    now = dt.datetime.now(_TZ)
    first = now.replace(day=1)
    prev = first - dt.timedelta(days=1)
    return prev.strftime("%Y-%m")


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
        """返回 (allowed, reject_dimension, warn_header_value)。
        无策略且无套餐=零判定(放行,零回归);任一超限=拒+审计。
        035:BOT 级(scope_ref 命中当前 key 绑定 Bot)与租户级双路独立判定,
        Bot 级先判先归因(拒绝维度文案带 BOT: 前缀)。"""
        all_p = await self._repo.list_policies(tenant_id=tenant_id)
        policies = {p["dimension"]: p for p in all_p if p.get("scope") == "TENANT"}
        bot_pols = {
            p["dimension"]: p
            for p in all_p
            if p.get("scope") == "BOT" and bot_id and str(p.get("scope_ref")) == bot_id
        }
        plan = await self._repo.get_tenant_plan(tenant_id=tenant_id)
        # 无任何配置=零判定(spec「未配置零回归」)
        if not policies and not bot_pols and plan is None:
            return True, "", ""
        month = _current_month()
        warns: list[str] = []
        # ── Bot 级(035;TOKENS/COST 两维,无套餐兜底=0 不限) ──
        if bot_pols:
            bot_tokens = await self._repo.month_bot_token_usage(
                tenant_id=tenant_id, bot_id=str(bot_id), month=month
            )
            if "TOKENS" in bot_pols:
                bp = bot_pols["TOKENS"]
                vb = check_quota(
                    used=bot_tokens,
                    limit=int(bp["limit_value"]),
                    warn_pct=int(bp["warn_pct"]),
                    dimension="TOKENS",
                )
                if not vb.allowed:
                    await self._audit(
                        "billing.quota_reject",
                        tenant_id,
                        {
                            "scope": "BOT",
                            "bot_id": str(bot_id),
                            "dimension": "TOKENS",
                            "used": bot_tokens,
                            "limit": int(bp["limit_value"]),
                        },
                    )
                    return False, "BOT:TOKENS", ""
                if vb.warn and int(bp["limit_value"]) > 0:
                    warns.append(f"BOT:TOKENS {bot_tokens}/{int(bp['limit_value'])}")
            if "COST" in bot_pols and int(bot_pols["COST"]["limit_value"]) > 0:
                bp = bot_pols["COST"]
                pm = int(plan["price_per_mtok"]) if plan else 0
                bot_cost = estimate_cost(
                    tokens=bot_tokens, calls=0, price_per_mtok=pm, price_per_call=0
                )
                vc = check_quota(
                    used=bot_cost,
                    limit=int(bp["limit_value"]),
                    warn_pct=int(bp["warn_pct"]),
                    dimension="COST",
                )
                if not vc.allowed:
                    await self._audit(
                        "billing.quota_reject",
                        tenant_id,
                        {
                            "scope": "BOT",
                            "bot_id": str(bot_id),
                            "dimension": "COST",
                            "used": bot_cost,
                            "limit": int(bp["limit_value"]),
                        },
                    )
                    return False, "BOT:COST", ""
                if vc.warn:
                    warns.append(f"BOT:COST {bot_cost}/{int(bp['limit_value'])}")
        # ── 租户级(034 原逻辑,scope=TENANT 行) ──
        tokens = await self._repo.month_token_usage(tenant_id=tenant_id, month=month)
        calls = await self._repo.month_call_count(tenant_id=tenant_id, month=month)
        # 限额解析:策略优先,套餐兜底(0=不限)
        token_limit = (
            int(policies["TOKENS"]["limit_value"])
            if "TOKENS" in policies
            else (int(plan["monthly_tokens"]) if plan else 0)
        )
        call_limit = (
            int(policies["CALLS"]["limit_value"])
            if "CALLS" in policies
            else (int(plan["monthly_calls"]) if plan else 0)
        )
        cost_limit = int(policies["COST"]["limit_value"]) if "COST" in policies else 0

        # R1-A1:按维各用各策略 warn_pct(缺省 80;多策略取 max 会吞单策略线)
        def _wp(dim: str) -> int:
            return int(policies[dim]["warn_pct"]) if dim in policies else 80

        vt = check_quota(used=tokens, limit=token_limit, warn_pct=_wp("TOKENS"), dimension="TOKENS")
        if not vt.allowed:
            await self._audit(
                "billing.quota_reject",
                tenant_id,
                {"dimension": "TOKENS", "used": tokens, "limit": token_limit},
            )
            return False, "TOKENS", ""
        vc = check_quota(used=calls, limit=call_limit, warn_pct=_wp("CALLS"), dimension="CALLS")
        if not vc.allowed:
            await self._audit(
                "billing.quota_reject",
                tenant_id,
                {"dimension": "CALLS", "used": calls, "limit": call_limit},
            )
            return False, "CALLS", ""
        vcost = None
        if cost_limit > 0:
            pm = int(plan["price_per_mtok"]) if plan else 0
            pc = int(plan["price_per_call"]) if plan else 0
            cost = estimate_cost(tokens=tokens, calls=calls, price_per_mtok=pm, price_per_call=pc)
            vcost = check_quota(used=cost, limit=cost_limit, warn_pct=_wp("COST"), dimension="COST")
            if not vcost.allowed:
                await self._audit(
                    "billing.quota_reject",
                    tenant_id,
                    {"dimension": "COST", "used": cost, "limit": cost_limit},
                )
                return False, "COST", ""
        # 预警头(任一维达线;R1-A1:COST 分支此前漏算;035:BOT 级 warn 已入列)
        if vt.warn and token_limit > 0:
            warns.append(f"TOKENS {tokens}/{token_limit}")
        if vc.warn and call_limit > 0:
            warns.append(f"CALLS {calls}/{call_limit}")
        if vcost is not None and vcost.warn and cost_limit > 0:
            warns.append(f"COST {vcost.used}/{cost_limit}")
        return True, "", "; ".join(warns)

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
        # 035:scope 校验(BOT 级=该租户内 Bot;CALLS 维无 bot 归属不立约)
        if scope not in ("TENANT", "BOT"):
            raise BillingValidationError("scope 须为 TENANT/BOT")
        if scope == "BOT":
            if dimension == "CALLS":
                raise BillingValidationError("CALLS 维度不支持 BOT 级配额")
            if not scope_ref:
                raise BillingValidationError("BOT 级须指定 scope_ref(bot_id)")
            if not await self._repo.bot_exists_in_tenant(bot_id=scope_ref, tenant_id=tenant_id):
                raise BillingNotFound("BOT_NOT_FOUND")
        else:
            scope_ref = None  # TENANT 强制空(CHECK 互斥;防脏数据)
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

    # ── 套餐 U/D(035 挂账④) ──

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

    # ── 支付闭环(035) ──

    def _channel(self, channel: str) -> PaymentChannel:
        ch = self._channels.get(channel)
        if ch is None:
            raise PaymentChannelClosed("CHANNEL_UNAVAILABLE")
        return ch

    async def create_payment(self, *, invoice_id: str, tenant_id: str) -> dict[str, Any]:
        """门户发起支付:每账单一在途单(旧单先惰性过期再清场)。
        R1-S1/A1:条件插入原子兜底竞态——并发窗口(读后判 PENDING→插入前
        被支付/作废/并发双击)返回 None,重读分流:非 PENDING=409;仍有
        在途单=双击幂等返回现有单(不新建不重复审计)。"""
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
        """**唯一核销点**(design D1):两入口(mock-simulate/callback)汇聚。
        验签→金额复核→惰性过期→CAS→invoice 同事务联动→审计;
        重复回调/终态单据收敛幂等响应(通道重试是常态,报错诱发无限重试)。
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
            # R1-T2:幂等败者重读——并发双回调败者拿到的快照可能已陈旧
            fresh = await self._repo.get_payment(payment_id=payment_id)
            return {
                "idempotent": True,
                "status": str(fresh["status"]) if fresh else str(pay["status"]),
            }
        linked = await self._repo.mark_invoice_paid(invoice_id=str(pay["invoice_id"]))
        if not linked:
            # R1-S1:单已 PAID 而账单非 PENDING(极端并发:作废/重复支付窗)——
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
        """mock 通道「支付动作」入口(收银台按钮):服务端真实生成签名后
        走统一核销路径——前端不持密钥、不直连改单(design D1)。"""
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
        """lifespan 每日薄壳批量兜底(design D3)。"""
        rows = await self._repo.expire_overdue_payments()
        for r in rows:
            await self._audit(
                "payment.expired",
                str(r["id"]),
                {"invoice_id": str(r["invoice_id"]), "via": "sweeper"},
            )
        return len(rows)
