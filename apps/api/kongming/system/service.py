"""system 域用例(007):管理面 CRUD 编排与安全保护规则。

零框架依赖(宪法 Article II):持久化经 SystemRepo Protocol 反转。
保护规则(rbac delta 007「内置数据保护」「角色删除前置校验」等)集中在
对应用例入口,先校验后落库,单用例一事务。
"""

from collections.abc import Mapping

from kongming.auth import password as pwd
from kongming.auth.ports import RefreshFamilyStore
from kongming.system.models import (
    BUILTIN_ROLE_KEYS,
    AdminAccountProtected,
    BuiltinRoleProtected,
    CustomDeptScopeInvalid,
    Dept,
    DeptInUse,
    DeptNode,
    DeptNotFound,
    InvalidDeptParent,
    PermItem,
    RoleInUse,
    RoleKeyImmutable,
    RoleKeyTaken,
    RoleListItem,
    RoleNotFound,
    SelfDeletion,
    SelfDisable,
    TenantItem,
    UnknownDeptId,
    UnknownPermId,
    UnknownRoleId,
    UnknownTenantId,
    UserListItem,
    UsernameTaken,
    UserNotFound,
)
from kongming.system.ports import SystemRepo
from kongming.uuid7 import uuid7


def build_dept_tree(depts: list[Dept]) -> list[DeptNode]:
    """全量行 → 树(内存组树;孤儿节点挂到根级,防脏数据丢失)。"""
    nodes = {d.id: DeptNode(dept=d) for d in depts}
    roots: list[DeptNode] = []
    for d in depts:
        node = nodes[d.id]
        parent = nodes.get(d.parent_id) if d.parent_id else None
        if parent is not None and parent is not node:
            parent.children.append(node)
        else:
            roots.append(node)
    return roots


def dept_subtree_ids(depts: list[Dept], root_id: str) -> set[str]:
    """root_id 的全部后代 id(防成环校验:新父不得为自身或后代)。"""
    children_of: dict[str | None, list[str]] = {}
    for d in depts:
        children_of.setdefault(d.parent_id, []).append(d.id)
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        cur = stack.pop()
        for child in children_of.get(cur, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


class SystemService:
    def __init__(
        self,
        repo: SystemRepo,
        admin_username: str,
        families: RefreshFamilyStore,
        refresh_ttl_sec: int,
    ) -> None:
        self._repo = repo
        self._admin_username = admin_username
        self._families = families
        self._refresh_ttl_sec = refresh_ttl_sec

    # ---- 用户 ----

    async def list_users(
        self, *, page: int, size: int, keyword: str
    ) -> tuple[int, list[UserListItem]]:
        kw = keyword.strip()
        total = await self._repo.count_users(kw)
        items = await self._repo.list_users(kw, size, (page - 1) * size)
        return total, items

    async def create_user(
        self,
        *,
        actor_id: str,
        username: str,
        password: str,
        nickname: str,
        email: str | None,
        status: str,
        dept_id: str | None,
        tenant_id: str,
        role_ids: list[str],
    ) -> str:
        if await self._repo.username_taken(username):
            raise UsernameTaken(f"用户名 {username} 已被占用")
        await self._assert_roles(role_ids)
        if not await self._repo.tenant_exists(tenant_id):
            raise UnknownTenantId("租户不存在")
        if dept_id is not None and not await self._repo.dept_exists(dept_id):
            raise UnknownDeptId("部门不存在")
        user_id = str(uuid7())
        await self._repo.insert_user(
            user_id=user_id,
            username=username,
            email=email,
            # 首登改密标志=未改(password_changed_at 缺省 NULL,rbac spec「创建用户」)
            password_hash=pwd.hash_password(password),
            nickname=nickname,
            status=status,
            dept_id=dept_id,
        )
        await self._repo.rebind_user_roles(user_id, role_ids)
        await self._repo.rebind_user_tenant(user_id, tenant_id)
        await self._repo.log_oper(actor_id, "user.create", username, {"user_id": user_id})
        await self._repo.commit()
        return user_id

    async def update_user(
        self,
        *,
        actor_id: str,
        user_id: str,
        nickname: str,
        email: str | None,
        status: str,
        dept_id: str | None,
        tenant_id: str,
        role_ids: list[str],
        password: str | None,
    ) -> None:
        current = await self._repo.get_user(user_id)
        if current is None:
            raise UserNotFound("用户不存在")
        username = str(current["username"])
        if status == "DISABLED":
            self._assert_disable_allowed(current, actor_id)
        # 超管账号保护(007 R1):不可删除,亦不可禁用/剥离 SUPER_ADMIN 绑定
        if username == self._admin_username:
            super_id = await self._repo.role_id_by_key("SUPER_ADMIN")
            if super_id is not None and super_id not in role_ids:
                raise AdminAccountProtected("超管账号不可剥离 SUPER_ADMIN 角色")
        await self._assert_roles(role_ids)
        if not await self._repo.tenant_exists(tenant_id):
            raise UnknownTenantId("租户不存在")
        if dept_id is not None and not await self._repo.dept_exists(dept_id):
            raise UnknownDeptId("部门不存在")
        await self._repo.update_user(
            user_id,
            email=email,
            nickname=nickname,
            status=status,
            dept_id=dept_id,
            # 编辑不传密码则不改(rbac spec);传了则重新哈希
            password_hash=None if not password else pwd.hash_password(password),
        )
        await self._repo.rebind_user_roles(user_id, role_ids)
        await self._repo.rebind_user_tenant(user_id, tenant_id)
        await self._repo.log_oper(actor_id, "user.update", username, {"user_id": user_id})
        await self._repo.commit()
        # 密码被管理员变更 → 吊销全部 refresh 家族(与自助改密同语义,R1 严重 1:
        # 否则疑似泄漏场景下重置密码后攻击者旧 refresh token 仍可续换 access)
        # 禁用同样踢下线(012):编辑路径与专用端点两路同收口,任一禁用路径生效
        if password or status == "DISABLED":
            await self._revoke_refresh(user_id)

    async def reset_password(self, *, actor_id: str, user_id: str, new_password: str) -> None:
        current = await self._repo.get_user(user_id)
        if current is None:
            raise UserNotFound("用户不存在")
        # 重置后首登改密标志复位(rbac spec「编辑与重置密码」;007 S1 修复:
        # 专用语句同步置 NULL,update_user 的 COALESCE 路径不触碰该列)
        await self._repo.reset_password(user_id, pwd.hash_password(new_password))
        await self._repo.log_oper(
            actor_id, "user.reset_password", str(current["username"]), {"user_id": user_id}
        )
        await self._repo.commit()
        # 重置=强制踢下线:吊销全部 refresh 家族(自助改密同语义)
        await self._revoke_refresh(user_id)

    async def enable_user(self, *, actor_id: str, user_id: str) -> None:
        """专用启用(012):只翻状态;启用不涉及吊销。"""
        current = await self._repo.get_user(user_id)
        if current is None:
            raise UserNotFound("用户不存在")
        await self._repo.set_user_status(user_id, "ACTIVE")
        await self._repo.log_oper(
            actor_id, "user.enable", str(current["username"]), {"user_id": user_id}
        )
        await self._repo.commit()

    async def disable_user(self, *, actor_id: str, user_id: str) -> None:
        """专用禁用(012):保护与编辑路径同构;禁用即踢下线(吊销 refresh 家族)。"""
        current = await self._repo.get_user(user_id)
        if current is None:
            raise UserNotFound("用户不存在")
        self._assert_disable_allowed(current, actor_id)
        await self._repo.set_user_status(user_id, "DISABLED")
        await self._repo.log_oper(
            actor_id, "user.disable", str(current["username"]), {"user_id": user_id}
        )
        await self._repo.commit()
        await self._revoke_refresh(user_id)

    def _assert_disable_allowed(self, current: Mapping[str, object], actor_id: str) -> None:
        """禁用保护单点(012 F10):编辑路径与专用端点同调,防两份漂移。"""
        if str(current["id"]) == actor_id:
            raise SelfDisable("不可禁用自己的账号")
        if str(current["username"]) == self._admin_username:
            raise AdminAccountProtected("超管账号不可禁用")

    async def _revoke_refresh(self, user_id: str) -> None:
        """吊销全部 refresh 家族(改密/重置/禁用三路同管线,012 收口单点)。"""
        await self._families.revoke_all_user_families(user_id, self._refresh_ttl_sec)

    async def delete_user(self, *, actor_id: str, user_id: str) -> None:
        current = await self._repo.get_user(user_id)
        if current is None:
            raise UserNotFound("用户不存在")
        if str(current["username"]) == self._admin_username:
            raise AdminAccountProtected("超管账号不可删除")
        if user_id == actor_id:
            raise SelfDeletion("不可删除自己的账号")
        await self._repo.soft_delete_user(user_id)
        await self._repo.log_oper(
            actor_id, "user.delete", str(current["username"]), {"user_id": user_id}
        )
        await self._repo.commit()

    async def _assert_roles(self, role_ids: list[str]) -> None:
        if not role_ids:
            return
        existing = await self._repo.roles_exist(role_ids)
        missing = sorted(set(role_ids) - set(existing))
        if missing:
            raise UnknownRoleId(f"角色不存在:{', '.join(missing)}")

    # ---- 角色 ----

    async def list_roles(self) -> list[RoleListItem]:
        return await self._repo.list_roles()

    async def create_role(
        self,
        *,
        actor_id: str,
        role_key: str,
        role_name: str,
        data_scope: str,
        sql_scope: str,
        sort: int,
        dept_ids: list[str] | None = None,
    ) -> str:
        if await self._repo.role_key_taken(role_key):
            raise RoleKeyTaken(f"角色键 {role_key} 已存在")
        target_depts = await self._resolve_custom_depts(data_scope, dept_ids)
        role_id = str(uuid7())
        await self._repo.insert_role(
            role_id=role_id,
            role_key=role_key,
            role_name=role_name,
            data_scope=data_scope,
            sql_scope=sql_scope,
            sort=sort,
        )
        await self._repo.rebind_role_depts(role_id, target_depts)
        await self._repo.log_oper(actor_id, "role.create", role_key, {"role_id": role_id})
        await self._repo.commit()
        return role_id

    async def update_role(
        self,
        *,
        actor_id: str,
        role_id: str,
        role_key: str,
        role_name: str,
        data_scope: str,
        sql_scope: str,
        sort: int,
        dept_ids: list[str] | None = None,
    ) -> None:
        current = await self._repo.get_role(role_id)
        if current is None:
            raise RoleNotFound("角色不存在")
        if str(current["role_key"]) != role_key:
            raise RoleKeyImmutable("role_key 创建后不可修改")
        target_depts = await self._resolve_custom_depts(data_scope, dept_ids)
        await self._repo.update_role(
            role_id,
            role_name=role_name,
            data_scope=data_scope,
            sql_scope=sql_scope,
            sort=sort,
        )
        await self._repo.rebind_role_depts(role_id, target_depts)
        await self._repo.log_oper(actor_id, "role.update", role_key, {"role_id": role_id})
        await self._repo.commit()

    async def _resolve_custom_depts(self, data_scope: str, dept_ids: list[str] | None) -> list[str]:
        """CUSTOM ↔ 部门集合搭配校验,返回落库目标集合(整包替换语义)。

        三条规则(spec datascope/rbac):
        - CUSTOM 且集合为空(键缺失或显式空数组)→ 拒:否则落一个「每次查询必被拒」的角色;
        - CUSTOM 且含不存在的部门 → UnknownDeptId(与用户绑部门同一族异常);
        - 非 CUSTOM 却带非空集合 → 拒:静默忽略会留下「配了部门却不生效」的隐性状态。
        非 CUSTOM 恒返回空集合 → rebind 即清空,库里不残留。
        """
        ids = dept_ids or []
        if data_scope != "CUSTOM":
            if ids:
                raise CustomDeptScopeInvalid("data_scope 非 CUSTOM 时不可指定部门集合")
            return []
        if not ids:
            raise CustomDeptScopeInvalid("data_scope=CUSTOM 必须至少选择一个部门")
        existing = await self._repo.depts_exist(ids)
        missing = sorted(set(ids) - set(existing))
        if missing:
            raise UnknownDeptId(f"部门不存在:{', '.join(missing)}")
        return sorted(set(ids))

    async def delete_role(self, *, actor_id: str, role_id: str) -> None:
        current = await self._repo.get_role(role_id)
        if current is None:
            raise RoleNotFound("角色不存在")
        if str(current["role_key"]) in BUILTIN_ROLE_KEYS:
            raise BuiltinRoleProtected("内置角色不可删除")
        if await self._repo.role_has_active_users(role_id):
            raise RoleInUse("角色仍绑定未软删用户,不可删除")
        await self._repo.delete_role(role_id)
        await self._repo.log_oper(
            actor_id, "role.delete", str(current["role_key"]), {"role_id": role_id}
        )
        await self._repo.commit()

    async def bind_role_perms(self, *, actor_id: str, role_id: str, perm_ids: list[str]) -> None:
        current = await self._repo.get_role(role_id)
        if current is None:
            raise RoleNotFound("角色不存在")
        if perm_ids:
            existing = await self._repo.perms_exist(perm_ids)
            missing = sorted(set(perm_ids) - set(existing))
            if missing:
                raise UnknownPermId(f"权限点不存在:{', '.join(missing)}")
        # 覆盖式全量同步(rbac spec「绑定权限为全量同步」;空数组=清空)
        await self._repo.rebind_role_perms(role_id, perm_ids)
        await self._repo.log_oper(
            actor_id, "role.bind_perms", str(current["role_key"]), {"count": len(perm_ids)}
        )
        await self._repo.commit()

    async def list_perms(self) -> list[PermItem]:
        return await self._repo.list_perms()

    # ---- 部门 ----

    async def dept_tree(self) -> list[DeptNode]:
        return build_dept_tree(await self._repo.list_depts())

    async def create_dept(
        self,
        *,
        actor_id: str,
        parent_id: str | None,
        dept_name: str,
        tenant_id: str,
        sort: int,
    ) -> str:
        if parent_id is not None and not await self._repo.dept_exists(parent_id):
            raise UnknownDeptId("父部门不存在")
        if not await self._repo.tenant_exists(tenant_id):
            raise UnknownTenantId("租户不存在")
        dept_id = str(uuid7())
        await self._repo.insert_dept(
            dept_id=dept_id,
            parent_id=parent_id,
            dept_name=dept_name,
            tenant_id=tenant_id,
            sort=sort,
        )
        await self._repo.log_oper(actor_id, "dept.create", dept_name, {"dept_id": dept_id})
        await self._repo.commit()
        return dept_id

    async def update_dept(
        self,
        *,
        actor_id: str,
        dept_id: str,
        parent_id: str | None,
        dept_name: str,
        tenant_id: str,
        sort: int,
        status: str,
    ) -> None:
        current = await self._repo.get_dept(dept_id)
        if current is None:
            raise DeptNotFound("部门不存在")
        if parent_id is not None:
            if not await self._repo.dept_exists(parent_id):
                raise UnknownDeptId("父部门不存在")
            # 新父落在以自身为根的后代集合内即成环(环会令递归查询/组树死循环);
            # 「父设为祖先」是合法的树上移,不在拒绝之列
            if parent_id == dept_id or parent_id in dept_subtree_ids(
                await self._repo.list_depts(), dept_id
            ):
                raise InvalidDeptParent("父部门不可为自身或其后代")
        if not await self._repo.tenant_exists(tenant_id):
            raise UnknownTenantId("租户不存在")
        await self._repo.update_dept(
            dept_id,
            parent_id=parent_id,
            dept_name=dept_name,
            tenant_id=tenant_id,
            sort=sort,
            status=status,
        )
        await self._repo.log_oper(actor_id, "dept.update", dept_name, {"dept_id": dept_id})
        await self._repo.commit()

    async def delete_dept(self, *, actor_id: str, dept_id: str) -> None:
        current = await self._repo.get_dept(dept_id)
        if current is None:
            raise DeptNotFound("部门不存在")
        if await self._repo.dept_has_children(dept_id):
            raise DeptInUse("部门存在子部门,不可删除")
        if await self._repo.dept_has_active_users(dept_id):
            raise DeptInUse("部门仍绑定未软删用户,不可删除")
        await self._repo.delete_dept(dept_id)
        await self._repo.log_oper(actor_id, "dept.delete", current.dept_name, {"dept_id": dept_id})
        await self._repo.commit()

    # ---- 租户(只读清单) ----

    async def list_tenants(self) -> list[TenantItem]:
        return await self._repo.list_tenants()
