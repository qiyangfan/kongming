"""billing 存储:策略/套餐/账单 + 用量聚合(sys_llm_usage
服务端采集为唯一事实源,不经客户端)。"""

import json
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.uuid7 import uuid7


class SqlBillingRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── 策略 ──

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
        # ON CONFLICT 表达式须与 0023 唯一索引逐字一致(PG index inference)
        await self._s.execute(
            text(
                "INSERT INTO public.sys_quota_policy"
                " (id, tenant_id, dimension, limit_value, warn_pct, scope, scope_ref)"
                " VALUES (:i, CAST(:t AS uuid), :d, :l, :w, :s,"
                "         CAST(:sr AS uuid))"
                " ON CONFLICT (tenant_id, dimension, period, scope,"
                "  COALESCE(scope_ref, '00000000-0000-0000-0000-000000000000'::uuid))"
                " DO UPDATE SET limit_value=EXCLUDED.limit_value,"
                " warn_pct=EXCLUDED.warn_pct, updated_at=now()"
            ),
            {
                "i": str(uuid7()),
                "t": tenant_id,
                "d": dimension,
                "l": limit_value,
                "w": warn_pct,
                "s": scope,
                "sr": scope_ref,
            },
        )

    async def list_policies(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_quota_policy"
                        " WHERE tenant_id=CAST(:t AS uuid) ORDER BY dimension"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def delete_policy(self, *, policy_id: str, tenant_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "DELETE FROM public.sys_quota_policy"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": policy_id, "t": tenant_id},
            ),
        )
        return res.rowcount > 0

    # ── 套餐 ──

    async def create_plan(self, **kw: Any) -> str:
        rid = str(uuid7())
        await self._s.execute(
            text(
                "INSERT INTO public.sys_billing_plan"
                " (id, plan_key, name, monthly_tokens, monthly_calls,"
                "  price_per_mtok, price_per_call)"
                " VALUES (:i, :k, :n, :t, :c, :pm, :pc)"
            ),
            {
                "i": rid,
                "k": kw["plan_key"],
                "n": kw["name"],
                "t": kw["monthly_tokens"],
                "c": kw["monthly_calls"],
                "pm": kw["price_per_mtok"],
                "pc": kw["price_per_call"],
            },
        )
        return rid

    async def list_plans(self) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text("SELECT * FROM public.sys_billing_plan ORDER BY created_at DESC, id DESC")
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # ── 套餐查改删 ──

    async def get_plan(self, *, plan_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text("SELECT * FROM public.sys_billing_plan WHERE id=CAST(:i AS uuid)"),
                    {"i": plan_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def update_plan(self, *, plan_id: str, **kw: Any) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_billing_plan SET"
                    " name=:n, monthly_tokens=:t, monthly_calls=:c,"
                    " price_per_mtok=:pm, price_per_call=:pc"
                    " WHERE id=CAST(:i AS uuid)"
                ),
                {
                    "i": plan_id,
                    "n": kw["name"],
                    "t": kw["monthly_tokens"],
                    "c": kw["monthly_calls"],
                    "pm": kw["price_per_mtok"],
                    "pc": kw["price_per_call"],
                },
            ),
        )
        return res.rowcount > 0

    async def count_plan_bindings(self, *, plan_id: str) -> int:
        return int(
            (
                await self._s.execute(
                    text("SELECT count(*) FROM public.sys_tenant WHERE plan_id=CAST(:p AS uuid)"),
                    {"p": plan_id},
                )
            ).scalar_one()
        )

    async def delete_plan(self, *, plan_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text("DELETE FROM public.sys_billing_plan WHERE id=CAST(:i AS uuid)"),
                {"i": plan_id},
            ),
        )
        return res.rowcount > 0

    # ── 配置面简列与归属校验 ──

    async def list_tenants_brief(self) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, tenant_key, tenant_name FROM public.sys_tenant"
                        " ORDER BY tenant_key"
                    )
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def bot_exists_in_tenant(self, *, bot_id: str, tenant_id: str) -> bool:
        """BOT 级策略 scope_ref 复校:Bot 属该租户才合法(跨租户 404)。"""
        return (
            await self._s.execute(
                text(
                    "SELECT 1 FROM public.sys_bot_definition"
                    " WHERE id=CAST(:b AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"b": bot_id, "t": tenant_id},
            )
        ).scalar_one_or_none() is not None

    async def list_bots_brief(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """BOT 级策略配置面 Bot 下拉:本租户 Bot 简列(挂 MANAGE 自供,
        解耦 bot:manage——与 list_tenants_brief 同解耦原则)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, bot_key, name FROM public.sys_bot_definition"
                        " WHERE tenant_id=CAST(:t AS uuid) ORDER BY bot_key"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def user_exists_in_tenant(self, *, user_id: str, tenant_id: str) -> bool:
        """USER 级策略 scope_ref 复校:用户存在(未软删)且绑定该租户
        才合法(跨租户/不存在 404)。"""
        return (
            await self._s.execute(
                text(
                    "SELECT 1 FROM public.sys_user u"
                    " JOIN public.sys_user_tenant ut ON ut.user_id=u.id"
                    " WHERE u.id=CAST(:u AS uuid) AND ut.tenant_id=CAST(:t AS uuid)"
                    " AND u.deleted_at IS NULL"
                ),
                {"u": user_id, "t": tenant_id},
            )
        ).scalar_one_or_none() is not None

    async def list_users_brief(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """USER 级策略配置面用户下拉:本租户用户简列(挂 MANAGE 自供,
        解耦 user:manage——与 list_bots_brief 同解耦原则;软删不列)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT u.id, u.username, u.nickname FROM public.sys_user u"
                        " JOIN public.sys_user_tenant ut ON ut.user_id=u.id"
                        " WHERE ut.tenant_id=CAST(:t AS uuid) AND u.deleted_at IS NULL"
                        " ORDER BY u.username"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def team_exists_in_tenant(self, *, team_id: str, tenant_id: str) -> bool:
        """TEAM 级策略 scope_ref 复校:团队属该租户才合法(跨租户 404)。"""
        return (
            await self._s.execute(
                text(
                    "SELECT 1 FROM public.sys_team"
                    " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                ),
                {"i": team_id, "t": tenant_id},
            )
        ).scalar_one_or_none() is not None

    async def list_teams_brief(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """TEAM 级策略配置面团队下拉:本租户团队简列(挂 MANAGE 自供,
        解耦 team:manage——与 list_bots_brief 同解耦原则)。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT id, team_key, name FROM public.sys_team"
                        " WHERE tenant_id=CAST(:t AS uuid) ORDER BY team_key"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def bot_scope_info(self, *, bot_id: str, tenant_id: str) -> dict[str, Any] | None:
        """网关判定归属解析:Bot 的 created_by(USER 级命中)与
        team_id(TEAM 级命中)一次点查;Bot 不在该租户返回 None(防御)。"""
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT created_by, team_id FROM public.sys_bot_definition"
                        " WHERE id=CAST(:b AS uuid) AND tenant_id=CAST(:t AS uuid)"
                    ),
                    {"b": bot_id, "t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_tenant_plan(self, *, tenant_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT p.* FROM public.sys_tenant t"
                        " JOIN public.sys_billing_plan p ON p.id=t.plan_id"
                        " WHERE t.id=CAST(:t AS uuid)"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def bind_plan(self, *, tenant_id: str, plan_id: str | None) -> None:
        await self._s.execute(
            text("UPDATE public.sys_tenant SET plan_id=CAST(:p AS uuid) WHERE id=CAST(:t AS uuid)"),
            {"p": plan_id, "t": tenant_id},
        )

    # ── 用量聚合(采集为唯一事实源) ──

    async def month_token_usage(self, *, tenant_id: str, month: str) -> int:
        """当月 token 合计(prompt+completion;month=YYYY-MM)。"""
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0)"
                        " FROM public.sys_llm_usage"
                        " WHERE tenant_id=CAST(:t AS uuid)"
                        " AND to_char(created_at AT TIME ZONE 'Asia/Shanghai',"
                        "        'YYYY-MM') = :m"
                    ),
                    {"t": tenant_id, "m": month},
                )
            ).scalar_one()
        )

    async def month_call_count(self, *, tenant_id: str, month: str) -> int:
        """当月 api 渠道调用数(sys_tool_audit channel='api' 计数)。"""
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT count(*) FROM public.sys_tool_audit"
                        " WHERE tenant_id=CAST(:t AS uuid) AND channel='api'"
                        " AND to_char(created_at AT TIME ZONE 'Asia/Shanghai',"
                        "        'YYYY-MM') = :m"
                    ),
                    {"t": tenant_id, "m": month},
                )
            ).scalar_one()
        )

    async def month_bot_token_usage(self, *, tenant_id: str, bot_id: str, month: str) -> int:
        """当月单 Bot token 合计(BOT 级配额;bot_id 归属=同步采集口径)。"""
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0)"
                        " FROM public.sys_llm_usage"
                        " WHERE tenant_id=CAST(:t AS uuid)"
                        " AND bot_id=CAST(:b AS uuid)"
                        " AND to_char(created_at AT TIME ZONE 'Asia/Shanghai',"
                        "        'YYYY-MM') = :m"
                    ),
                    {"t": tenant_id, "b": bot_id, "m": month},
                )
            ).scalar_one()
        )

    async def month_user_token_usage(self, *, tenant_id: str, user_id: str, month: str) -> int:
        """当月单用户 token 合计(USER 级配额):用量行经 Bot 归属联表
        解析(usage.bot_id → bot.created_by);用户被软删后聚合归零
        (策略保留但零命中,不级联)。"""
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT COALESCE(SUM(u.prompt_tokens + u.completion_tokens), 0)"
                        " FROM public.sys_llm_usage u"
                        " JOIN public.sys_bot_definition b ON b.id = u.bot_id"
                        " JOIN public.sys_user su ON su.id = b.created_by"
                        " WHERE u.tenant_id=CAST(:t AS uuid)"
                        " AND b.tenant_id=CAST(:t AS uuid)"
                        " AND b.created_by=CAST(:usr AS uuid)"
                        " AND su.deleted_at IS NULL"
                        " AND to_char(u.created_at AT TIME ZONE 'Asia/Shanghai',"
                        "        'YYYY-MM') = :m"
                    ),
                    {"t": tenant_id, "usr": user_id, "m": month},
                )
            ).scalar_one()
        )

    async def month_team_token_usage(self, *, tenant_id: str, team_id: str, month: str) -> int:
        """当月单团队 token 合计(TEAM 级配额):用量行经资源归属联表
        解析(usage.bot_id → bot.team_id,零用量表 schema 变更);资源换队
        跟随现归属(联表即定义,当月治理窗口语义);团队被删除后聚合归零
        (策略保留但零命中,不级联)。"""
        return int(
            (
                await self._s.execute(
                    text(
                        "SELECT COALESCE(SUM(u.prompt_tokens + u.completion_tokens), 0)"
                        " FROM public.sys_llm_usage u"
                        " JOIN public.sys_bot_definition b ON b.id = u.bot_id"
                        " WHERE u.tenant_id=CAST(:t AS uuid)"
                        " AND b.tenant_id=CAST(:t AS uuid)"
                        " AND b.team_id=CAST(:tm AS uuid)"
                        " AND EXISTS (SELECT 1 FROM public.sys_team tm"
                        "             WHERE tm.id=CAST(:tm AS uuid)"
                        "             AND tm.tenant_id=CAST(:t AS uuid))"
                        " AND to_char(u.created_at AT TIME ZONE 'Asia/Shanghai',"
                        "        'YYYY-MM') = :m"
                    ),
                    {"t": tenant_id, "tm": team_id, "m": month},
                )
            ).scalar_one()
        )

    async def month_usage_by_model(self, *, tenant_id: str, month: str) -> list[dict[str, Any]]:
        """按模型聚合(账单 usage_json 明细)。calls=成功调用次数
        (FILTER status='SUCCESS',ERROR 不计);tokens=实际消耗全量,
        与月账单口径 month_token_usage 一致(ERROR 行带回 usage 的
        tokens 也计);by_model tokens 求和=月总 tokens。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT model, SUM(prompt_tokens) AS prompt,"
                        " SUM(completion_tokens) AS completion,"
                        " count(*) FILTER (WHERE status = 'SUCCESS') AS calls"
                        " FROM public.sys_llm_usage"
                        " WHERE tenant_id=CAST(:t AS uuid)"
                        " AND to_char(created_at AT TIME ZONE 'Asia/Shanghai',"
                        "        'YYYY-MM') = :m"
                        " GROUP BY model ORDER BY model"
                    ),
                    {"t": tenant_id, "m": month},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # ── 账单 ──

    async def insert_invoice(
        self, *, tenant_id: str, period: str, usage_json: dict[str, Any], amount: int
    ) -> str | None:
        """幂等出账(tenant+period 唯一,冲突=已出账返回 None)。"""
        rid = str(uuid7())
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "INSERT INTO public.sys_invoice"
                    " (id, tenant_id, period, usage_json, amount)"
                    " VALUES (:i, CAST(:t AS uuid), :p, CAST(:u AS jsonb), :a)"
                    " ON CONFLICT (tenant_id, period) DO NOTHING"
                ),
                {
                    "i": rid,
                    "t": tenant_id,
                    "p": period,
                    "u": json.dumps(usage_json, ensure_ascii=False, default=str),
                    "a": amount,
                },
            ),
        )
        return rid if res.rowcount > 0 else None

    async def list_invoices(self, *, tenant_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_invoice"
                        " WHERE tenant_id=CAST(:t AS uuid) ORDER BY period DESC, id DESC"
                    ),
                    {"t": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def get_invoice(self, *, invoice_id: str, tenant_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_invoice"
                        " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                    ),
                    {"i": invoice_id, "t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_invoice_admin(self, *, invoice_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text("SELECT * FROM public.sys_invoice WHERE id=CAST(:i AS uuid)"),
                    {"i": invoice_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def page_all_invoices(self, *, page: int, size: int) -> tuple[list[dict[str, Any]], int]:
        """管理面账单分页:每行附最新支付单状态。"""
        total = int(
            (await self._s.execute(text("SELECT count(*) FROM public.sys_invoice"))).scalar_one()
        )
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT i.id, i.tenant_id, i.period, i.amount, i.status,"
                        " i.created_at, t.tenant_key,"
                        " p.id AS payment_id, p.status AS payment_status"
                        " FROM public.sys_invoice i"
                        " JOIN public.sys_tenant t ON t.id=i.tenant_id"
                        " LEFT JOIN LATERAL ("
                        "  SELECT id, status FROM public.sys_payment sp"
                        "  WHERE sp.invoice_id=i.id"
                        "  ORDER BY sp.created_at DESC LIMIT 1"
                        " ) p ON true"
                        " ORDER BY i.period DESC, i.created_at DESC"
                        " LIMIT :sz OFFSET :off"
                    ),
                    {"sz": size, "off": (page - 1) * size},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows], total

    async def list_tenants_without_invoice(self, *, period: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._s.execute(
                    text(
                        "SELECT t.id, t.plan_id FROM public.sys_tenant t"
                        " WHERE NOT EXISTS ("
                        "  SELECT 1 FROM public.sys_invoice i"
                        "  WHERE i.tenant_id=t.id AND i.period=:p)"
                    ),
                    {"p": period},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # ── 支付单:状态机转换一律 CAS(WHERE status='CREATED'),
    # 并发/重放安全由数据库行级原子性兜底 ──

    async def insert_payment_if_pending(
        self,
        *,
        invoice_id: str,
        tenant_id: str,
        channel: str,
        amount: int,
        expires_at: Any,
    ) -> str | None:
        """条件插入:INSERT...SELECT 仅账单 PENDING 才落行,原子消除
        读后判 PENDING 与插入间被支付/作废的竞态窗口;SAVEPOINT 内捕获
        部分唯一索引违例(并发双击同在途)返回 None,由服务层重读分流
        (双击幂等返回现有单/409)。"""
        from sqlalchemy.exc import IntegrityError

        rid = str(uuid7())
        try:
            async with self._s.begin_nested():
                res = cast(
                    "CursorResult[object]",
                    await self._s.execute(
                        text(
                            "INSERT INTO public.sys_payment"
                            " (id, invoice_id, tenant_id, channel, amount, expires_at)"
                            " SELECT :i, v.id, CAST(:t AS uuid), :c, :a, :e"
                            " FROM public.sys_invoice v"
                            " WHERE v.id=CAST(:v AS uuid)"
                            " AND v.tenant_id=CAST(:t AS uuid)"
                            " AND v.status='PENDING'"
                        ),
                        {
                            "i": rid,
                            "v": invoice_id,
                            "t": tenant_id,
                            "c": channel,
                            "a": amount,
                            "e": expires_at,
                        },
                    ),
                )
        except IntegrityError:
            return None
        return rid if res.rowcount > 0 else None

    async def get_payment(self, *, payment_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text("SELECT * FROM public.sys_payment WHERE id=CAST(:i AS uuid)"),
                    {"i": payment_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_payment_for_tenant(
        self, *, payment_id: str, tenant_id: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_payment"
                        " WHERE id=CAST(:i AS uuid) AND tenant_id=CAST(:t AS uuid)"
                    ),
                    {"i": payment_id, "t": tenant_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def cancel_open_payments(self, *, invoice_id: str) -> int:
        """同账单在途单全置 CANCELLED(重付前清场);返回受影响行数。"""
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_payment SET status='CANCELLED',"
                    " updated_at=now()"
                    " WHERE invoice_id=CAST(:v AS uuid) AND status='CREATED'"
                ),
                {"v": invoice_id},
            ),
        )
        return int(res.rowcount)

    async def cas_pay(self, *, payment_id: str, trade_no: str) -> bool:
        """核销 CAS:仅 CREATED→PAID;rowcount=0=重复回调/已取消/已过期。"""
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_payment SET status='PAID',"
                    " trade_no=:tn, paid_at=now(), updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND status='CREATED'"
                ),
                {"i": payment_id, "tn": trade_no},
            ),
        )
        return res.rowcount > 0

    async def cas_cancel_payment(self, *, payment_id: str) -> bool:
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_payment SET status='CANCELLED',"
                    " updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND status='CREATED'"
                ),
                {"i": payment_id},
            ),
        )
        return res.rowcount > 0

    async def mark_invoice_paid(self, *, invoice_id: str) -> bool:
        """账单联动:仅 PENDING→PAID(VOID 后付款不回写)。"""
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_invoice SET status='PAID'"
                    " WHERE id=CAST(:v AS uuid) AND status='PENDING'"
                ),
                {"v": invoice_id},
            ),
        )
        return res.rowcount > 0

    async def cas_void_invoice(self, *, invoice_id: str) -> bool:
        """作废 CAS:仅 PENDING→VOID(已 PAID/已 VOID rowcount=0)。"""
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_invoice SET status='VOID'"
                    " WHERE id=CAST(:v AS uuid) AND status='PENDING'"
                ),
                {"v": invoice_id},
            ),
        )
        return res.rowcount > 0

    async def expire_overdue_payments(self) -> list[dict[str, Any]]:
        """批量兜底:过期在途单全部置 EXPIRED,返回行供逐条审计。"""
        rows = (
            (
                await self._s.execute(
                    text(
                        "UPDATE public.sys_payment SET status='EXPIRED',"
                        " updated_at=now()"
                        " WHERE status='CREATED' AND expires_at < now()"
                        " RETURNING id, tenant_id, invoice_id"
                    )
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def expire_payment_if_overdue(self, *, payment_id: str) -> bool:
        """惰性过期(读取/核销路径):单单判定。"""
        res = cast(
            "CursorResult[object]",
            await self._s.execute(
                text(
                    "UPDATE public.sys_payment SET status='EXPIRED',"
                    " updated_at=now()"
                    " WHERE id=CAST(:i AS uuid) AND status='CREATED'"
                    " AND expires_at < now()"
                ),
                {"i": payment_id},
            ),
        )
        return res.rowcount > 0

    async def latest_payment_for_invoice(self, *, invoice_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._s.execute(
                    text(
                        "SELECT * FROM public.sys_payment"
                        " WHERE invoice_id=CAST(:v AS uuid)"
                        " ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"v": invoice_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def commit(self) -> None:
        await self._s.commit()
