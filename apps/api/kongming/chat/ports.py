"""chat 域端口(typing.Protocol 反转,宪法 Article II)。

ExplainRunner 由组合根(router 适配层)绑定 internal.service.explain——chat 域
service/models 受 import-linter 零框架契约,不可间接 import internal.service
(携 sqlalchemy 引擎层),007 uuid7 拆分同款教训;适配层组合与 009 rag⇄datasource 同构。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExplainOutcome:
    """执行计划领域结果(chat 域视角,与 internal ExplainResult 解耦)。"""

    verdict: str  # PASS / BLOCKED / ERROR
    reason: str | None = None
    plan: object | None = None


class ExplainRunner(Protocol):
    """执行计划运行端口;完整防线(防火墙/改写/复检/只读池)由实现侧保证。"""

    async def explain(
        self,
        *,
        trace_id: str,
        tenant_key: str,
        datasource_key: str,
        sql_text: str,
        user_id: str,
    ) -> ExplainOutcome: ...
