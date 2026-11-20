"""semantic 端口绑定:调用者数据域解析适配(datascope 判定原语的组合)。

放在 router 之外:rag 召回段复用本适配器,路由之间不互相 import。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.semantic.models import CallerScope


class CallerDeptScopeAdapter:
    """CallerDeptScopeResolver 实现——组合 datascope 判定原语
    (user_role_scopes→most_permissive→部门集合解析),semantic 域零 datascope
    import(lint-imports 契约)。CUSTOM 空集合按读面降级为空集合(仅 ALL 档),
    不复用执行面 fail-closed 语义(分层:注入方向=收紧,无越权面)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resolve(self, *, user_id: str, tenant_id: str) -> CallerScope:
        from kongming.datascope.repository import SqlDataScopeRepo
        from kongming.datascope.service import most_permissive

        del tenant_id  # data_scope 判定原语为全租户身份体系,租户维度由授权闸保证
        repo = SqlDataScopeRepo(self._s)
        scopes = await repo.user_role_scopes(user_id)
        if not scopes:
            return CallerScope(universal=False)
        eff = most_permissive(scopes)
        if eff == "ALL":
            return CallerScope(universal=True)
        if eff == "SELF":
            return CallerScope(universal=False)
        ids: list[str] = []
        if eff == "CUSTOM":
            ids = await repo.custom_dept_ids(user_id)
        else:
            exists, dept = await repo.user_dept_id(user_id)
            if not exists or dept is None:
                return CallerScope(universal=False)
            ids = await repo.dept_and_below(dept) if eff == "DEPT_AND_BELOW" else [dept]
        return CallerScope(universal=False, dept_ids=tuple(dict.fromkeys(ids)))
