"""mcp 领域模型(028,零框架):配置/发现工具/命名与冲突纯函数。

命名规则:mcp__<serverKey>__<toolName>;readOnlyHint 缺省 None →
写判定 fail-closed(spec「未标注工具走确认流」)。
"""

import re
from dataclasses import dataclass, field
from typing import Any

# 与 datasource key 同款形态(008);fullmatch 判定在领域层(尾换行绕过教训)
SERVER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

MCP_NAME_PREFIX = "mcp__"

# 审查循环 F1:回溯型正则(嵌套量词,如 `(a+)+`/`(\w+)*`)在 jsonschema 的
# pattern 校验里可对长输入产生灾难性回溯(实测 (a+)+b 对 a*28 耗时 17s+,
# 纯 CPU 卡死单事件循环 worker)。启发式检测:量词修饰的组内又含量词。
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*?]")
_PATTERN_LEN_CAP = 512


def has_redos_pattern(schema: dict[str, Any]) -> bool:
    """递归扫描 JSON Schema 的全部 pattern 值,命中回溯型/超长形态。

    纯函数;sync 期调用,命中即整体降级 {"type":"object"}(L4 退化为结构
    校验,不再执行 pattern 匹配)。启发式非完备,覆盖实证的灾难性回溯族。
    """
    stack: list[object] = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "pattern" and isinstance(v, str):
                    if len(v) > _PATTERN_LEN_CAP or _NESTED_QUANTIFIER_RE.search(v):
                        return True
                else:
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return False


class McpServerExists(Exception):
    """租户内 server_key 冲突。"""


class McpServerNotFound(Exception):
    """不存在/跨租户同形/禁用竞态。"""


@dataclass(frozen=True)
class DiscoveredTool:
    """tools/list 发现的工具快照(read_only=readOnlyHint 判定结果)。"""

    name: str  # 原始工具名(server 侧)
    description: str
    schema: dict[str, Any]
    read_only: bool  # fail-closed:未标注=False(按写工具)


def qualified_name(server_key: str, tool_name: str) -> str:
    return f"{MCP_NAME_PREFIX}{server_key}__{tool_name}"


def parse_qualified(name: str) -> tuple[str, str] | None:
    """mcp__<key>__<tool> → (key, tool);非 MCP 名 → None。
    server_key 形态校验(不含 __),tool 名允许任意非空残余。"""
    if not name.startswith(MCP_NAME_PREFIX):
        return None
    rest = name[len(MCP_NAME_PREFIX):]
    key, sep, tool = rest.partition("__")
    if not sep or not tool or not SERVER_KEY_RE.fullmatch(key):
        return None
    return key, tool


@dataclass(frozen=True)
class ServerConfig:
    """配置视图(查询面;headers 已掩码,明文永不出仓储层)。"""

    id: str
    tenant_id: str
    server_key: str
    name: str
    endpoint: str
    enabled: bool
    header_keys: list[str]  # 仅键名(值掩码 ***)
    tools: list[dict[str, Any]] = field(default_factory=list)
    last_sync_at: str | None = None
    last_sync_error: str = ""


# R1-A4:注册全名上限——ToolExecuteIn.tool_name 与 sys_tool_audit.tool_name
# 双闸均 64;超名工具注册即不可用的死工具,同步期跳过并告警
_QUALIFIED_MAX = 64


def merge_discovered(
    server_key: str,
    discovered: list[DiscoveredTool],
    *,
    _seen: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """快照组装(纯函数):全名拼装+冲突/超长检测。

    返回 (快照 entries, 告警列表);同名冲突先注册方胜(本函数内按传入
    序),全名超 64 字符跳过(R1-A4)——均记入告警。_seen 供跨 server
    合并调用方传入(目录聚合时),单 server 同步时为空。
    """
    seen = _seen if _seen is not None else set()
    entries: list[dict[str, Any]] = []
    conflicts: list[str] = []
    for t in discovered:
        qname = qualified_name(server_key, t.name)
        if len(qname) > _QUALIFIED_MAX:
            conflicts.append(f"超长跳过: {qname[:40]}…")
            continue
        if qname in seen:
            conflicts.append(qname)
            continue
        seen.add(qname)
        entries.append(
            {
                "name": t.name,
                "qualified": qname,
                "description": t.description,
                "schema": t.schema,
                "read_only": t.read_only,
            }
        )
    return entries, conflicts
