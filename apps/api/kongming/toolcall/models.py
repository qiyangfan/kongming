"""toolcall 领域模型(019):工具元数据/执行结果/参数摘要与幂等键纯函数。

零框架纪律:本模块不 import fastapi/sqlalchemy/redis(import-linter 守护)。
spec: tool-calling「工具定义与注册表」「工具纵深防御」。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

# 审计/领域语义的执行状态(DB CHECK 同集,大写)
ToolStatus = Literal[
    "EXECUTED",
    "REJECTED",
    "ERROR",
    "RATE_LIMITED",
    "PENDING_CONFIRM",
    "CONFIRM_REJECTED",
    "CONFIRM_EXPIRED",
]

# 参数摘要中值须脱敏的键名(小写比对)——凭证类字段永不进审计/日志;
# 审查循环 F3:MCP 动态工具参数名不受控,白名单须覆盖常见凭证命名族
_SENSITIVE_ARG_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
    }
)

ARGS_DIGEST_MAX = 500  # 审计用参数摘要截断上限


class ToolSpec(TypedDict):
    """注入 Agent 面的工具元数据(OpenAI tools 参数直译素材;唯一真理源=api 注册表)。"""

    name: str
    description: str
    parameters: dict[str, object]  # JSON Schema
    write: bool
    require_confirm: bool


@dataclass(frozen=True)
class ToolDef:
    """注册表项:注入元数据 + 控制面内部绑定(权限点/handler 键同名)。"""

    name: str
    description: str
    parameters: dict[str, object]
    perm_code: str
    write: bool
    require_confirm: bool

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            write=self.write,
            require_confirm=self.require_confirm,
        )


@dataclass(frozen=True)
class ToolOutcome:
    """工具执行终态(六层防御产出);PENDING_CONFIRM 时 confirm_id 非空。"""

    status: ToolStatus
    reason: str | None = None
    result: dict[str, object] | None = None  # EXECUTED 时工具结果载荷
    confirm_id: str | None = None
    duration_ms: int = 0


def args_digest(args: dict[str, object]) -> str:
    """审计用参数摘要(纯函数):敏感键值脱敏 + 定长截断。"""
    masked = {k: ("***" if k.lower() in _SENSITIVE_ARG_KEYS else v) for k, v in args.items()}
    return json.dumps(masked, ensure_ascii=False, default=str)[:ARGS_DIGEST_MAX]


def derive_idempotency_key(*, user_id: str, tool_name: str, args: dict[str, object]) -> str:
    """调用方未提供幂等键时的确定性派生:sha256(user|tool|canonical args)。"""
    canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f"{user_id}|{tool_name}|{canonical}".encode()).hexdigest()


# ── 领域异常(service 抛出,router 映射;与 internal/sql/execute 同形) ──


class TenantNotFound(Exception):
    """tenant_key 不存在或已禁用(对外同形,不暴露是哪种)。"""


class TenantBindingForbidden(Exception):
    """用户未绑定目标租户(fail-closed)。"""


class ToolPermissionDenied(Exception):
    """角色集不含工具权限点(经 outcome=REJECTED 落审计的正常路径不使用;

    仅确认端点复校等需要 HTTP 语义的分支抛出)。"""


class ConfirmNotFound(Exception):
    """待确认记录不存在/不属当前用户/已处理:404 同形,不暴露存在性。"""


class KnowledgeSourceError(Exception):
    """081:个人知识库检索失败(分类随 message 前缀上浮:unreachable/protocol)。

    unreachable=连接层未送达(拒连/连接超时/accept 后无响应关连;实现侧已按
    只读分层重试一次);protocol=响应类错误(非 2xx/报文畸形/读超时)。
    分发兜底映射 ERROR + 审计,agent 据实降级为观察,不编造检索结果。
    """


@dataclass(frozen=True)
class ToolAuditEntry:
    """工具执行审计写入载荷(领域层纯数据;仓储落 sys_tool_audit)。"""

    trace_id: str
    user_id: str
    tenant_id: str
    tool_name: str
    status: str  # ToolStatus(大写枚举)
    args_digest: str | None = None
    args_json: dict[str, Any] | None = None  # 仅 PENDING_CONFIRM(确认重放用)
    error_reason: str | None = None
    duration_ms: int | None = None
    datasource_key: str | None = None
    channel: str = "chat"  # 025:渠道标记(chat/web_embed/api)
    idempotency_key: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class PendingConfirm:
    """待确认行(FOR UPDATE 锁定后读出)。"""

    id: str
    user_id: str
    tenant_id: str
    tool_name: str
    args: dict[str, Any]
    idempotency_key: str | None
    expires_at: datetime | None
    status: str
