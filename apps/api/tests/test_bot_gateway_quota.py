# pyright: reportPrivateUsage=false
"""网关配额改读权益快照后的回归:缺失 fail-closed、未超放行、超限 429 并审计。"""

from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.seed import seed_all
from kongming.config import get_settings
from kongming.control.dependencies import install_control_runtime
from kongming.control.entitlement import QuotaLimits
from kongming.control.models import EntitlementMissing


class _Entitlements:
    def __init__(self, limits: QuotaLimits | None = None, error: Exception | None = None) -> None:
        self._limits = limits
        self._error = error

    async def for_tenant_id(self, tenant_id: str) -> QuotaLimits:
        if self._error is not None:
            raise self._error
        assert self._limits is not None
        return self._limits


class _Runtime:
    def __init__(self, entitlements: _Entitlements) -> None:
        self.entitlements = entitlements


async def test_gateway_quota_fail_closed_and_daily_limit(session: AsyncSession) -> None:
    from kongming.bot.router import _check_gateway_quota

    await seed_all(session, get_settings())
    tenant_id = (
        await session.execute(text("SELECT id FROM public.sys_tenant WHERE tenant_key='default'"))
    ).scalar_one()
    bot_id = str(uuid4())
    key_info: dict[str, object] = {"tenant_id": str(tenant_id), "bot_id": bot_id}
    try:
        install_control_runtime(cast(Any, _Runtime(_Entitlements(error=EntitlementMissing("无")))))
        with pytest.raises(HTTPException) as denied:
            await _check_gateway_quota(key_info)
        assert denied.value.status_code == 503

        install_control_runtime(
            cast(
                Any,
                _Runtime(
                    _Entitlements(
                        QuotaLimits(
                            calls_daily=0,
                            calls_monthly=0,
                            bot_calls_daily=2,
                            tool_calls_per_minute=0,
                        )
                    )
                ),
            )
        )
        allowed, dimension, _ = await _check_gateway_quota(key_info)
        assert allowed is True and dimension == ""

        await session.execute(
            text(
                "INSERT INTO public.sys_llm_usage"
                " (id, tenant_id, bot_id, model, task, prompt_tokens, completion_tokens)"
                " VALUES (gen_random_uuid(), CAST(:t AS uuid), CAST(:b AS uuid),"
                "  'qwen-plus', 'REACT', 1, 1)"
            ),
            {"t": str(tenant_id), "b": bot_id},
        )
        await session.commit()

        install_control_runtime(
            cast(
                Any,
                _Runtime(
                    _Entitlements(
                        QuotaLimits(
                            calls_daily=0,
                            calls_monthly=0,
                            bot_calls_daily=1,
                            tool_calls_per_minute=0,
                        )
                    )
                ),
            )
        )
        allowed, dimension, _ = await _check_gateway_quota(key_info)
        assert allowed is False and dimension == "BOT_DAILY_CALLS"
        audit = (
            await session.execute(
                text(
                    "SELECT count(*) FROM public.sys_oper_log"
                    " WHERE action='bot.quota_reject' AND target=:b"
                ),
                {"b": bot_id},
            )
        ).scalar_one()
        assert int(audit) == 1
    finally:
        install_control_runtime(None)
