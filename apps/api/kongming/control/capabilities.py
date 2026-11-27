"""能力/权益键单一来源:注册上报、健康与只读投影共用,禁止各处另写清单。"""

SERVICE_ID = "kongming"

# 服务能力键(与锦官 service-registry 的 capabilities 契约对应;X-01 冻结前
# 以本清单为唯一事实源,契约测试按此对拍)
CAPABILITIES: tuple[str, ...] = (
    "chat",
    "nl2sql",
    "rag",
    "documents",
    "tools",
    "mcp",
    "connectors",
    "workflows",
    "triggers",
    "bots",
    "reports",
    "tasks",
    "voice",
    "admin-api",
    "service-read-api",
)

# 管理台可见的服务 admin API 能力组(不暴露具体路由,只声明有写入口)
ADMIN_CAPABILITIES: tuple[str, ...] = (
    "datasource.manage",
    "rag.manage",
    "semantic.manage",
    "tool.manage",
    "mcp.manage",
    "connector.manage",
    "workflow.manage",
    "trigger.manage",
    "bot.manage",
    "guardrail.manage",
    "role.manage",
    "dept.manage",
    "team.manage",
    "command.local",
    "command.control",
)

# 会被控制面用于权益判定的键(本地只声明可执行维度,不提交任何额度)
ENTITLEMENT_KEYS: tuple[str, ...] = (
    "chat.calls",
    "bot.runs",
    "tool.calls",
    "report.generation",
    "task.execution",
)

# 只读投影分节(锦官按节拉取;未知节 404)
READONLY_SECTIONS: tuple[str, ...] = (
    "summary",
    "datasources",
    "rag",
    "semantic",
    "tools",
    "mcp",
    "connectors",
    "workflows",
    "triggers",
    "bots",
    "guardrails",
    "identity",
)
