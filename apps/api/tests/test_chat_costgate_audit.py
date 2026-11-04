# pyright: reportPrivateUsage=false
"""chat 成本闸审计回归:独立短会话审计写入必须落库(add 后显式提交)。

缺陷史:短会话 add 后不 commit,close 即回滚=审计静默丢失(quality 域
同款翻车;本测试钉住 chat costgate 的收口形态)。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_cost_gate_oper_log_persists(session: AsyncSession) -> None:
    from kongming.auth.seed import seed_all
    from kongming.chat.router import _build_cost_gate
    from kongming.config import get_settings

    await seed_all(session, get_settings())
    gate = _build_cost_gate()
    oper = gate._oper
    assert oper is not None

    await oper.log(action="chat_cost_gate_test", target="tenant-1", detail={"k": "v"})

    row = (
        await session.execute(
            text("SELECT count(*) FROM public.sys_oper_log WHERE action='chat_cost_gate_test'")
        )
    ).scalar_one()
    assert int(row) == 1  # 缺提交时此断言红(行被回滚吞掉)
