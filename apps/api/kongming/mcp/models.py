"""mcp 领域模型:配置、工具发现、命名和冲突处理。

命名规则:mcp__<serverKey>__<toolName>;readOnlyHint 缺省 None →
写判定 fail-closed(spec「未标注工具走确认流」)。
资源/模板原语模型 + read_resource 合成工具 + 外部文本净化
(资源内容/模板预览进观察与展示前过既有注入检测)。
"""

import re
from dataclasses import dataclass, field
from typing import Any, cast

from kongming.guardrail.models import RuleConfig, evaluate_text
from kongming.guardrail.rules import REGISTRY

# 形态与 datasource key 一致;fullmatch 判定在领域层(防尾换行绕过)
SERVER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

MCP_NAME_PREFIX = "mcp__"

# 嵌套量词(如 `(a+)+`)可能让长输入触发灾难性回溯并占满事件循环。
# 此处用启发式检测量词组内再次包含量词的模式。
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*?]")
_PATTERN_LEN_CAP = 512


def has_redos_pattern(schema: dict[str, Any]) -> bool:
    """递归扫描 JSON Schema 的全部 pattern 值,命中回溯型/超长形态。

    纯函数;sync 期调用,命中即整体降级 {"type":"object"}(L4 退化为结构
    校验,不再执行 pattern 匹配)。启发式非完备,覆盖的灾难性回溯族。
    """
    stack: list[object] = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            # isinstance 收窄产物键型未知,收窄键型后再遍历(仅类型断言)
            for k, v in cast("dict[str, object]", node).items():
                if k == "pattern" and isinstance(v, str):
                    if len(v) > _PATTERN_LEN_CAP or _NESTED_QUANTIFIER_RE.search(v):
                        return True
                else:
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(cast("list[object]", node))
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


@dataclass(frozen=True)
class DiscoveredResource:
    """resources/list 发现的资源快照条目。

    mime_type 缺省空串=未声明:白名单不预设二进制,读取期按内容形态
    兜底(blob 拒绝);声明为二进制类则白名单校验期即拒(不出站)。"""

    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


@dataclass(frozen=True)
class PromptArgument:
    """提示模板参数声明。"""

    name: str
    description: str = ""
    required: bool = False


@dataclass(frozen=True)
class DiscoveredPrompt:
    """prompts/list 发现的提示模板快照条目(仅清单展示,渲染预览
    实时拉取不落库)。"""

    name: str
    description: str = ""
    arguments: tuple[PromptArgument, ...] = ()


@dataclass(frozen=True)
class ProbeResult:
    """三类原语并行探测的汇合形态(不支持与失败严格区分)。

    failures:原语名(tools/resources/prompts)→ (kind, detail) 结构化失败
    (超时/拒连/协议错,既有分类);unsupported:能力未声明/方法不存在
    (-32601)——计数 0,不算失败。「只有工具的简单 server」零感知。"""

    tools: list[DiscoveredTool] = field(default_factory=list[DiscoveredTool])
    resources: list[DiscoveredResource] = field(default_factory=list[DiscoveredResource])
    prompts: list[DiscoveredPrompt] = field(default_factory=list[DiscoveredPrompt])
    failures: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])
    unsupported: frozenset[str] = frozenset[str]()


def qualified_name(server_key: str, tool_name: str) -> str:
    return f"{MCP_NAME_PREFIX}{server_key}__{tool_name}"


def parse_qualified(name: str) -> tuple[str, str] | None:
    """mcp__<key>__<tool> → (key, tool);非 MCP 名 → None。
    server_key 形态校验(不含 __),tool 名允许任意非空残余。"""
    if not name.startswith(MCP_NAME_PREFIX):
        return None
    rest = name[len(MCP_NAME_PREFIX) :]
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
    # default_factory 走参数化泛型别名(bare list 推断出 Unknown 元素)
    tools: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    last_sync_at: str | None = None
    last_sync_error: str = ""


# 注册全名上限——ToolExecuteIn.tool_name 与 sys_tool_audit.tool_name
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
    序),全名超 64 字符跳过——均记入告警。_seen 供跨 server
    合并调用方传入(目录聚合时),单 server 同步时为空。
    """
    seen = _seen if _seen is not None else set[str]()
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


# ── 资源读取合成工具与原语快照组装 ──

READ_RESOURCE_TOOL = "read_resource"

# 文本 mime 白名单(二进制资源拒绝——观察是文本形态);
# 空 mimeType=未声明,不预设二进制(读取期按内容形态 blob 兜底)。
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIMES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/sql",
        "application/toml",
        "application/x-sh",
    }
)


def is_text_mime(mime: str) -> bool:
    """资源 mime 是否文本类:二进制类白名单校验期即拒(不出站)。"""
    m = mime.split(";", 1)[0].strip().lower()
    if not m:
        return True  # 未声明 → 读取期按内容形态判定
    return m.startswith(_TEXT_MIME_PREFIXES) or m in _TEXT_MIMES


def read_resource_tool() -> DiscoveredTool:
    """read_resource 合成工具(每 server 一个):读资源聚合为一个工具而非
    每资源一工具,收敛工具目录与注入集。"""
    return DiscoveredTool(
        name=READ_RESOURCE_TOOL,
        description="读取该 MCP server 已枚举的资源内容(uri 须来自其资源清单,白名单外拒绝)",
        schema={
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": "资源 uri(必须来自该 server 的资源清单)",
                }
            },
            "required": ["uri"],
        },
        read_only=True,  # 白名单约束的只读原语,不走确认流
    )


def resource_snapshot(resources: list[DiscoveredResource]) -> list[dict[str, Any]]:
    """资源快照存储形态(python 键,随 tools 快照同族入库 jsonb)。"""
    return [
        {
            "uri": r.uri,
            "name": r.name,
            "description": r.description,
            "mime_type": r.mime_type,
        }
        for r in resources
    ]


def prompt_snapshot(prompts: list[DiscoveredPrompt]) -> list[dict[str, Any]]:
    """模板快照存储形态(仅清单;渲染预览实时拉取不落库)。"""
    return [
        {
            "name": p.name,
            "description": p.description,
            "arguments": [
                {"name": a.name, "description": a.description, "required": a.required}
                for a in p.arguments
            ],
        }
        for p in prompts
    ]


# ── 外部文本净化(资源内容/模板预览进观察前的注入检测) ──

# 注入检测配置:REGISTRY 全部 input 规则缺省启用
_EXTERNAL_INPUT_CONFIGS: list[RuleConfig] = [
    RuleConfig(rule=r, enabled=True, action=r.action, threshold=r.threshold)
    for r in REGISTRY
    if r.guardrail_type == "input"
]

_INJECTION_NOTE = "[外部内容命中注入检测({names}),原文不进观察]"


def sanitize_external_text(text: str) -> tuple[str, list[str]]:
    """外部文本进观察/展示前的净化:blocked(注入形态)命中整体替换
    警示文案(fail-closed,与输出护栏 blocked→安全文案同构);masked(PII/
    凭证)命中原位脱敏。返回 (净化后文本, 命中规则名集)。"""
    cleaned, hits = evaluate_text(text, _EXTERNAL_INPUT_CONFIGS, "input")
    names = [h.rule_name for h in hits]
    blocked = [h.rule_name for h in hits if h.action == "blocked"]
    if blocked:
        return _INJECTION_NOTE.format(names=", ".join(blocked)), names
    return cleaned, names
