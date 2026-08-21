"""audit 域端口(015,typing.Protocol 反转,宪法 Article II)。

AuditRepo 镜像 AuditRepository 公开方法 + commit/rollback;
service 仅持本端口,不触碰 session(013 chat/014 feedback 同一反转范式)。
"""

from typing import Protocol


class AuditRepo(Protocol):
    """audit 查询持久化端口(015)。全部为只读查询 + 事务原语。"""

    async def user_has_scope_all(self, *, user_id: str) -> bool:
        """角色集含 data_scope=ALL 即 True(行级可见性判定,014 同款口径)。"""
        ...

    async def sql_page(
        self,
        *,
        vis_user_id: str | None,
        trace_id: str | None,
        user_id: str | None,
        tenant_id: str | None,
        verdict: str | None,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        """SQL 审计分页(total, rows);vis_user_id 非 None 强制本人(行级可见性)。"""
        ...

    async def sql_detail(self, *, audit_id: str) -> dict[str, object] | None:
        """单条详情(完整 sql_text);不存在返回 None。"""
        ...

    async def oper_page(
        self,
        *,
        vis_user_id: str | None,
        action: str | None,
        user_id: str | None,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        """操作日志分页(total, rows);vis_user_id 非 None 强制本人。"""
        ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
