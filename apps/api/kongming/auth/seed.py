"""启动幂等种子(rbac spec「种子数据」):角色/权限点/默认租户/超管。

纪律:只增不改——按唯一键查缺补插,从不 UPDATE/DELETE 既有行(不覆盖线上改动);
重复启动幂等。权限点=随代码发布的契约数据,注册表即唯一定义处。
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth import password as pwd
from kongming.auth.models import Role, Tenant, User
from kongming.auth.repository import SqlUnitOfWork
from kongming.auth.service import DEFAULT_ROLE_KEY, DEFAULT_TENANT_KEY
from kongming.config import Settings
from kongming.db import uuid7

DEFAULT_ADMIN_PASSWORD = "admin123"  # noqa: S105  # 缺省不安全密码:播种后强制首登改密兜底

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
    # 019 tool-calling:工具权限点(最小授权,按工具粒度)+ 工具管理面
    ("tool:sql:validate", "工具-SQL校验", 10),
    ("tool:sql:execute", "工具-SQL执行", 11),
    ("tool:rag:retrieve", "工具-知识检索", 12),
    ("tool:announcement:create", "工具-公告创建", 13),
    ("system:tool:manage", "工具管理", 14),
    # 020 prompt-guardrails:护栏规则/审计管理面(仅 SUPER_ADMIN,不推荐 DEVELOPER)
    ("system:guardrail:manage", "护栏管理", 15),
    # 021 multi-model-routing:LLM 用量聚合查询(仅 SUPER_ADMIN)
    ("system:llm-usage:view", "模型用量查看", 16),
    # 023 semantic-layer:语义层管理(仅 SUPER_ADMIN)
    ("system:semantic:manage", "语义层管理", 17),
    # 024 document-rag:文档管理(仅 SUPER_ADMIN)
    ("system:document:manage", "文档管理", 18),
    # 025:Bot 是用户资产(归属收口),无全局管理面权限点(R1-L1 摘死权限点)
    # 027 cloud-code-sandbox:代码执行(显式授予制,不进 DEVELOPER 推荐集——
    # 代码执行面风险高于只读 SQL,与公告写工具同哲学)
    ("tool:code:execute", "工具-代码执行", 19),
    # 028 mcp-interop:MCP 管理面(仅 SUPER_ADMIN)+ 工具使用(显式授予制)
    ("system:mcp:manage", "MCP服务管理", 20),
    ("tool:mcp:use", "工具-MCP调用", 21),
    # 029 workflow-orchestration:管理面(仅 SUPER_ADMIN)+ 执行
    # (进 DEVELOPER 推荐集——节点级防御以触发者身份逐层复校,无权限放大)
    ("system:workflow:manage", "工作流管理", 22),
    ("workflow:execute", "工作流执行", 23),
    # 031 triggers-webhooks:触发器管理面(仅 SUPER_ADMIN)
    ("system:trigger:manage", "触发器管理", 24),
    # 032 team-workspace:团队管理面(仅 SUPER_ADMIN;成员管理走团队 OWNER 闸)
    ("system:team:manage", "团队管理", 25),
    # 033 bot-store:商店消费面(bot:run;上架治理走 032 can_manage 无新管理点)
    ("bot:run", "Bot商店使用", 26),
    # 034 billing:开发者门户(自助门面)+ 计费管理面(仅超管)
    ("developer:portal", "开发者门户", 27),
    ("system:billing:manage", "计费管理", 28),
    # 057 numeric-grounding:确定性派生量计算(只读纯函数;进 DEVELOPER 推荐集)
    ("tool:compute", "工具-数值计算", 29),
    # 056 skill-pack:垂类内容包管理面(仅 SUPER_ADMIN)
    ("system:skill:manage", "Skill包管理", 30),
    # 069 chat-quality-observability:轮次质量观测(仅 SUPER_ADMIN)
    ("system:chat-quality:view", "问答质量查看", 31),
    # 081 external-knowledge:个人知识库检索(显式授予制,不进 DEVELOPER 推荐集——
    # 单租户个人部署由 SUPER_ADMIN 自动覆盖;多租户场景告警但不拒注册,082)
    ("tool:knowledge:search", "工具-个人知识库检索", 32),
    # 088 report-generation:分析报告(生成/读取/导出;进 DEVELOPER 推荐集——
    # 取数走既有只读链路逐层复校,无权限放大)
    ("report:generate", "报告生成", 33),
    ("report:read", "报告读取", 34),
    ("report:export", "报告导出", 35),
    # 090 task-kernel:持久任务(创建/读取/管理;进 DEVELOPER 推荐集——
    # 步骤执行逐层复校,无权限放大)
    ("task:create", "任务创建", 36),
    ("task:read", "任务读取", 37),
    ("task:manage", "任务管理", 38),
    # 094 web-search-domain:联网检索(显式授予制,不进 DEVELOPER 推荐集——
    # 查询内容外发第三方检索供应商 + API 成本,与 knowledge:search 同哲学;
    # SUPER_ADMIN 由增量逻辑自动覆盖)
    ("tool:websearch:search", "工具-联网搜索", 39),
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
        # 019:只读三件套(与 chat:execute 同哲学;公告写工具不推,显式授予制)
        "tool:sql:validate",
        "tool:sql:execute",
        "tool:rag:retrieve",
        # 057:确定性派生量计算(只读,与 SQL 只读三件套同哲学)
        "tool:compute",
        # 029:工作流执行(节点级防御逐层复校)
        "workflow:execute",
        # 033:商店消费面(与 chat:execute 同哲学)
        "bot:run",
        # 034:开发者门户(自助门面)
        "developer:portal",
        # 088:分析报告三件套(生成/读取/导出,与 chat:execute 同哲学)
        "report:generate",
        "report:read",
        "report:export",
        # 090:持久任务三件套(与 chat:execute 同哲学)
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

    # 超管:存在即跳过(含密码;在线改密后重播不覆盖)
    admin = await uow.users.get_by_username(settings.admin_username)
    if admin is None:
        env_password = settings.admin_initial_password
        admin = User(
            id=str(uuid7()),
            username=settings.admin_username,
            email=None,
            password_hash=pwd.hash_password(env_password or DEFAULT_ADMIN_PASSWORD),
            nickname="超管",
            status="ACTIVE",
            # env 密码视为已改密;缺省密码强制首登改密(spec 明文)
            password_changed_at=datetime.now(UTC) if env_password else None,
        )
        await uow.users.create(admin)
    if admin_role is not None:
        await uow.roles.bind_user(admin.id, admin_role.id)
    tenant = await uow.tenants.get_by_key(DEFAULT_TENANT_KEY)
    if tenant is not None:
        await uow.tenants.bind_user(admin.id, tenant.id)

    await uow.commit()
