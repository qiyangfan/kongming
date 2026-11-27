"""启动幂等种子(rbac spec「种子数据」):角色/权限点/默认租户/可选运维超管。

纪律:只增不改——按唯一键查缺补插,从不 UPDATE/DELETE 既有行(不覆盖线上改动);
重复启动幂等。权限点=随代码发布的契约数据,注册表即唯一定义处。

不播种任何可登录账号:账户归Keycloak(ADR-0010);超管只能由 `KM_ADMIN_SUBJECT`
绑定到Keycloak已存在的 sub,或由现有超管在管理面显式授权。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.models import Role, Tenant, User
from kongming.auth.repository import SqlUnitOfWork
from kongming.auth.service import DEFAULT_ROLE_KEY, DEFAULT_TENANT_KEY
from kongming.config import Settings
from kongming.db import uuid7

# 权限点注册表:(perm_code, perm_name, sort);业务权限点随各自域变更增补本清单
PERMISSIONS: list[tuple[str, str, int]] = [
    ("system:user:manage", "用户管理", 1),
    ("system:role:manage", "角色管理", 2),
    ("chat:execute", "问答执行", 3),
    ("system:datasource:manage", "数据源管理", 4),
    ("system:rag:manage", "知识库管理", 5),
    ("chat:feedback:submit", "反馈提交", 6),
    ("system:feedback:view", "反馈查看", 7),
    ("system:feedback:handle", "反馈处置", 8),
    ("system:audit:view", "审计查看", 9),
    ("tool:sql:validate", "工具-SQL校验", 10),
    ("tool:sql:execute", "工具-SQL执行", 11),
    ("tool:rag:retrieve", "工具-知识检索", 12),
    ("tool:announcement:create", "工具-公告创建", 13),
    ("system:tool:manage", "工具管理", 14),
    # 护栏管理面仅 SUPER_ADMIN(不进 DEVELOPER 推荐集)
    ("system:guardrail:manage", "护栏管理", 15),
    # 仅 SUPER_ADMIN
    ("system:llm-usage:view", "模型用量查看", 16),
    # 仅 SUPER_ADMIN
    ("system:semantic:manage", "语义层管理", 17),
    # 仅 SUPER_ADMIN
    ("system:document:manage", "文档管理", 18),
    # Bot 归属用户资产,无全局管理面权限点
    # 代码执行显式授予制:风险高于只读 SQL,不进 DEVELOPER 推荐集
    ("tool:code:execute", "工具-代码执行", 19),
    # MCP 管理面仅 SUPER_ADMIN,工具使用显式授予制
    ("system:mcp:manage", "MCP服务管理", 20),
    ("tool:mcp:use", "工具-MCP调用", 21),
    # 管理面仅 SUPER_ADMIN;执行进 DEVELOPER 推荐集:节点级防御以触发者身份逐层复校,无权限放大
    ("system:workflow:manage", "工作流管理", 22),
    ("workflow:execute", "工作流执行", 23),
    # 仅 SUPER_ADMIN
    ("system:trigger:manage", "触发器管理", 24),
    # 成员管理走团队 OWNER 闸,管理面仅 SUPER_ADMIN
    ("system:team:manage", "团队管理", 25),
    # 上架治理走 can_manage 校验,无新增管理点
    ("bot:run", "Bot商店使用", 26),
    ("developer:portal", "开发者门户", 27),
    ("system:billing:manage", "计费管理", 28),
    ("tool:compute", "工具-数值计算", 29),
    # 仅 SUPER_ADMIN
    ("system:skill:manage", "Skill包管理", 30),
    # 仅 SUPER_ADMIN
    ("system:chat-quality:view", "问答质量查看", 31),
    # 报告数据经受控只读执行并复核权限范围,进 DEVELOPER 推荐集
    ("report:generate", "报告生成", 33),
    ("report:read", "报告读取", 34),
    ("report:export", "报告导出", 35),
    # 步骤执行逐层复校,无权限放大,进 DEVELOPER 推荐集
    ("task:create", "任务创建", 36),
    ("task:read", "任务读取", 37),
    ("task:manage", "任务管理", 38),
    # 查询内容外发第三方供应商且有 API 成本,显式授予制
    ("tool:websearch:search", "工具-联网搜索", 39),
    # 连接器治理面仅 SUPER_ADMIN,工具调用显式授予制
    # (租户自定义端点出站 + 凭证治理,与 websearch 同哲学)
    ("connector:manage", "连接器管理", 42),
    ("tool:http:call", "工具-HTTP连接器调用", 43),
    # 控制面状态与本地命令(管理台);跨服务命令的提交入口在锦官,不在此
    ("system:control:view", "控制面状态查看", 44),
    ("system:control:manage", "本地运行命令", 45),
]

ROLES: list[tuple[str, str, str, str, int]] = [
    # (role_key, role_name, data_scope, sql_scope, sort)
    ("SUPER_ADMIN", "超级管理员", "ALL", "DML_DDL", 1),
    (DEFAULT_ROLE_KEY, "开发者", "SELF", "SELECT_ONLY", 2),
]

# 角色推荐绑定(只增不摘):SUPER_ADMIN 由下方逻辑增量补齐全部注册点,
# 其余内置角色按本清单增量绑定——不覆盖运行时的人工调整
ROLE_RECOMMENDED_PERMS: dict[str, list[str]] = {
    DEFAULT_ROLE_KEY: [
        "chat:execute",
        "chat:feedback:submit",
        # 只读三件套;公告写工具不推,显式授予制
        "tool:sql:validate",
        "tool:sql:execute",
        "tool:rag:retrieve",
        # 确定性派生量计算(只读)
        "tool:compute",
        # 工作流执行(节点级防御逐层复校)
        "workflow:execute",
        "bot:run",
        "developer:portal",
        "report:generate",
        "report:read",
        "report:export",
        "task:create",
        "task:read",
        "task:manage",
    ],
}

DEFAULT_TENANT_NAME = "默认租户"


async def seed_all(session: AsyncSession, settings: Settings) -> None:
    """幂等播种(可重复调用):全部写操作 ON CONFLICT DO NOTHING / 查缺补插。"""
    uow = SqlUnitOfWork(session)

    await uow.perms.insert_missing(PERMISSIONS)
    for role_key, role_name, data_scope, sql_scope, sort in ROLES:
        await uow.roles.insert(
            Role(
                id=str(uuid7()),
                role_key=role_key,
                role_name=role_name,
                data_scope=data_scope,
                sql_scope=sql_scope,
                sort=sort,
            )
        )
    await uow.tenants.insert(
        Tenant(id=str(uuid7()), tenant_key=DEFAULT_TENANT_KEY, tenant_name=DEFAULT_TENANT_NAME)
    )

    # SUPER_ADMIN 增量补齐全部注册权限点(新版新增权限点自动达超管;不摘既有绑定)
    admin_role = await uow.roles.get_by_key("SUPER_ADMIN")
    if admin_role is not None:
        await uow.roles.bind_perm_codes(admin_role.id, [c for c, _, _ in PERMISSIONS])

    # 内置角色推荐绑定增量补齐(只增不摘,ON CONFLICT DO NOTHING)
    for role_key, codes in ROLE_RECOMMENDED_PERMS.items():
        role = await uow.roles.get_by_key(role_key)
        if role is not None:
            await uow.roles.bind_perm_codes(role.id, codes)

    await _bind_admin(uow, settings)

    await uow.commit()


async def _bind_admin(uow: SqlUnitOfWork, settings: Settings) -> None:
    """把 `KM_ADMIN_SUBJECT` 指定的Keycloak sub 绑到 SUPER_ADMIN 与默认租户。

    只认Keycloak已建过档的 sub(用户经Keycloak登录一次后 sys_user 才有该行);未配置时
    不建任何账号。绑定只增:撤销超管须在管理面显式操作。
    """
    subject = (settings.admin_subject or "").strip()
    if not subject:
        return
    admin = await uow.users.get_by_id(subject)
    if admin is None:
        # 只为配置的Keycloak subject 预建授权资料(无密码、不可本地登录);
        # 同时写 identity,确保该 subject 之后经Keycloak登录直接命中本行。
        admin = User(
            id=subject,
            username="admin",
            email=None,
            nickname="运维管理员",
            status="ACTIVE",
        )
        await uow.users.create(admin)
        await uow.identities.create(admin.id, "KEYCLOAK", subject)
    admin_role = await uow.roles.get_by_key("SUPER_ADMIN")
    if admin_role is not None:
        await uow.roles.bind_user(admin.id, admin_role.id)
    tenant = await uow.tenants.get_by_key(DEFAULT_TENANT_KEY)
    if tenant is not None:
        await uow.tenants.bind_user(admin.id, tenant.id)
