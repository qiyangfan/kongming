"""sandbox 端口(027):运行器与审计存储反转,用例层零框架依赖。"""

from typing import Protocol

from kongming.sandbox.models import SandboxOutcome


class SandboxRunner(Protocol):
    """代码执行运行器(027):实现侧保证容器级隔离与资源硬配额。

    data=None 为纯计算形态(沙箱内无 df 变量);非 None 形态
    {"columns": [...], "rows": [[...]]}(JSON 安全值,源自六层防御取数)。
    实现侧 MUST 不抛运行时异常——一切失败收敛 SandboxOutcome(含
    TIMEOUT/RESOURCE_LIMIT/ERROR);运行时本身不可达由用例层在组合根
    期判定(未注入=未启用),运行期故障(镜像缺失等)收敛 ERROR。"""

    async def run(self, *, code: str, data: dict[str, object] | None) -> SandboxOutcome: ...


class SandboxRunWriter(Protocol):
    """执行审计(027):独立短事务落库(与 019 AuditWriter 同款),
    失败仅日志不阻断主链路;返回行 id(失败 None)。"""

    async def record(
        self,
        *,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        code: str,
        outcome: SandboxOutcome,
    ) -> str | None: ...
