"""sandbox 端口:运行器与审计存储反转,用例层零框架依赖。"""

from typing import Protocol

from kongming.sandbox.models import SandboxOutcome


class SandboxRunner(Protocol):
    """代码执行运行器:实现侧保证容器级隔离与资源硬配额。

    data=None 为纯计算形态(沙箱内无 df 变量);非 None 形态
    {"columns": [...], "rows": [[...]]}(JSON 安全值,源自六层防御取数)。
    实现侧 MUST 不抛运行时异常——一切失败收敛 SandboxOutcome(含
    TIMEOUT/RESOURCE_LIMIT/ERROR);运行时本身不可达由用例层在组合根
    期判定(未注入=未启用),运行期故障(镜像缺失等)收敛 ERROR。"""

    async def run(self, *, code: str, data: dict[str, object] | None) -> SandboxOutcome: ...


class SandboxRunWriter(Protocol):
    """沙箱运行审计写入端口。审计在独立短事务中保存；写入失败由调用方记录。"""

    async def record(
        self,
        *,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        code: str,
        outcome: SandboxOutcome,
    ) -> str | None: ...
