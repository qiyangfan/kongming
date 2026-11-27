"""共享策略包作用域注入点(叶子模块,避免 guardrail 反向依赖 control 运行时)。

未安装=不过滤(standalone);管理态由控制面运行时安装,按租户解析激活规则。
"""

from typing import Protocol


class PolicyScope(Protocol):
    async def rule_names_for_tenant(self, tenant_id: str) -> frozenset[str] | None: ...


_scope: PolicyScope | None = None


def install_policy_scope(scope: PolicyScope | None) -> None:
    global _scope
    _scope = scope


def get_policy_scope() -> PolicyScope | None:
    return _scope
