"""connector 领域模型(099 http-connector-domain):租户级 HTTP 连接器的
注册声明/注册校验/掩码/装配纯函数(零框架,宪法 Article II)。

注册校验是安全资产(fail-fast,spec「注册校验」):路径模板占位与参数声明
求交、白名单主机形态(精确主机,禁通配/路径/端口/scheme)、方法与鉴权白名单、
材料相容(bearer/api_key/basic 须有材料,none 不得携带)、超时部署级封顶。
写语义缺省规则(design D4):非 GET 缺省需确认,显式声明覆盖方法启发式。
凭证任何接口不回显明文:查询/列表面只呈现「有无+尾号掩码」(与 webhook
secret 同口径)。
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from kongming.toolgateway.models import CONNECTOR_TOOL_PREFIX

# key 形态(与 MCP server_key 同族):小写字母开头,字母/数字/下划线,2-64 位
CONNECTOR_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
# 参数名形态(占位符安全:字母/下划线开头,禁空白与花括号)
PARAM_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
# 白名单主机形态(精确主机:域名或字面 IP;禁 scheme/路径/通配/端口)
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$")

NAME_MAX = 128
DESCRIPTION_MAX = 512
PATH_TEMPLATE_MAX = 1024
PARAM_DESCRIPTION_MAX = 200
PARAM_MAX = 20
HOSTS_MAX = 10
SECRET_MAX = 2048
TIMEOUT_MIN_MS = 100
# basic 形态的材料为单串「user:password」(b64 编码前口径,注册表单注明)

METHODS: tuple[str, ...] = ("GET", "POST")
AUTH_TYPES: tuple[str, ...] = ("none", "bearer", "api_key", "basic")
# 材料型鉴权(none 之外都要材料)
SECRET_AUTH_TYPES: frozenset[str] = frozenset({"bearer", "api_key", "basic"})
PARAM_TYPES: tuple[str, ...] = ("string", "number", "boolean")
# 参数声明类型 → JSON Schema 类型(LLM 注入形态与策略器校验共用的有限集)
_PARAM_TYPE_TO_SCHEMA: dict[str, str] = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
}

# api_key 形态的凭证头名(「api-key 头」的既有惯例形态;单点防漂移)
API_KEY_HEADER = "X-API-Key"


class ConnectorValidationError(Exception):
    """领域层注册校验 400(008 McpValidationError 同哲学)。"""


class ConnectorNotFound(Exception):
    """连接器不存在/不属当前租户:404 同形,不暴露是哪种。"""


class ConnectorExists(Exception):
    """同租户 key 唯一冲突。"""


@dataclass(frozen=True)
class ParamDecl:
    """参数声明(design D5 有限字段集:名/类型/必填/描述;不收 JSON Schema)。"""

    name: str
    type: str  # string | number | boolean
    required: bool
    description: str


@dataclass(frozen=True)
class ConnectorDecl:
    """注册声明(管理面表单的领域形态;requires_confirmation=None=按方法缺省)。"""

    key: str
    name: str
    description: str
    method: str
    path_template: str
    params: tuple[ParamDecl, ...]
    allowed_hosts: tuple[str, ...]
    auth_type: str
    requires_confirmation: bool | None
    timeout_ms: int


def tool_name(key: str) -> str:
    """连接器行 key → Agent 面工具名(单源:注入装配与执行解析共用)。"""
    return f"{CONNECTOR_TOOL_PREFIX}{key}"


def key_from_tool_name(name: str) -> str | None:
    """工具名 → 连接器 key(非本域前缀返回 None;调用方不感知前缀细节)。"""
    if not name.startswith(CONNECTOR_TOOL_PREFIX):
        return None
    key = name[len(CONNECTOR_TOOL_PREFIX) :]
    return key if CONNECTOR_KEY_RE.fullmatch(key) else None


def resolve_confirmation(method: str, declared: bool | None) -> bool:
    """写语义缺省规则(design D4):非 GET 缺省需确认、GET 缺省豁免;
    显式声明胜过方法启发式(POST 只读端点可标读,GET 写端点可标写)。"""
    if declared is not None:
        return declared
    return method != "GET"


def _params_problem(params: tuple[ParamDecl, ...]) -> str | None:
    if len(params) > PARAM_MAX:
        return f"参数声明数量超上限({PARAM_MAX})"
    seen: set[str] = set()
    for p in params:
        if not PARAM_NAME_RE.fullmatch(p.name):
            return f"参数名非法:{p.name[:32] or '(空)'}"
        if p.name in seen:
            return f"参数名重复:{p.name}"
        seen.add(p.name)
        if p.type not in PARAM_TYPES:
            return f"参数 {p.name} 类型越白名单({p.type})"
        if len(p.description) > PARAM_DESCRIPTION_MAX:
            return f"参数 {p.name} 描述超长(上限 {PARAM_DESCRIPTION_MAX})"
    return None


def _hosts_problem(allowed_hosts: tuple[str, ...]) -> str | None:
    if not allowed_hosts:
        return "出口主机白名单不能为空"
    if len(allowed_hosts) > HOSTS_MAX:
        return f"白名单主机数量超上限({HOSTS_MAX})"
    for h in allowed_hosts:
        if not h or not HOSTNAME_RE.fullmatch(h):
            return f"白名单主机形态非法(须为精确主机,禁通配/路径/端口/scheme):{h[:64]}"
    return None


# 注册期 SSRF 字面判定(099 spec「注册期 SSRF 判定拦截」):与执行期判定
# (workflow.http_runner 的拒绝段)同一策略的零依赖副本——models 不 import
# http_runner(其模块级 import httpx,污染领域纯度),两份判定同判由
# tests/test_http_connector.py 的双源对拍测试钉住(guardrail 异常「单源
# 双副本」同哲学)。
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # 共享段(云内网/Tailscale)


def _is_denied_ip_literal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 逃逸面归一
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_reserved
        or ip.is_multicast
        or ip in _CGNAT
    )


def _denied_host_problem(allowed_hosts: tuple[str, ...], template: str) -> str | None:
    """白名单/模板主机的字面 IP 落拒绝段即拒;域名形态注册期不做解析判定
    (DNS 可变,执行期 assert_url_allowed 兜底——spec 双落点声明的分工)。"""
    for h in allowed_hosts:
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            continue
        if _is_denied_ip_literal(ip):
            return f"白名单主机不可指向内网/保留地址段:{h}"
    try:
        t_host = (urlsplit(template).hostname or "").lower()
    except ValueError:
        return None  # 模板畸形由 _template_problem 另行拒绝
    try:
        ip = ipaddress.ip_address(t_host)
    except ValueError:
        return None
    if _is_denied_ip_literal(ip):
        return f"路径模板主机不可指向内网/保留地址段:{t_host}"
    return None


def _template_problem(
    template: str, allowed_hosts: tuple[str, ...], params: tuple[ParamDecl, ...]
) -> str | None:
    if not template:
        return "路径模板不能为空"
    if len(template) > PATH_TEMPLATE_MAX:
        return f"路径模板超长(上限 {PATH_TEMPLATE_MAX})"
    try:
        parts = urlsplit(template)
    except ValueError:
        return "路径模板不是合法 URL"
    if parts.scheme not in ("http", "https"):
        return "路径模板仅允许 http/https"
    host = (parts.hostname or "").lower()
    if not host:
        return "路径模板缺主机"
    if parts.query or parts.fragment:
        return "路径模板不得携带 query/fragment(查询参数由参数声明按名映射)"
    # 占位符与参数声明求交:每个占位必须有同名声明,且不得重复出现
    declared = {p.name for p in params}
    seen: set[str] = set()
    for raw in re.findall(r"\{([^{}]+)\}", template):
        if raw not in declared:
            return f"路径占位 {{{raw}}} 未在参数声明中定义"
        if raw in seen:
            return f"路径占位 {{{raw}}} 重复出现"
        seen.add(raw)
    # 模板主机须命中白名单(「注入了却执行必拒」的注册期一致闸;执行期
    # 出口校验仍是独立防线,防存量行/带外改行)
    if host not in {h.lower() for h in allowed_hosts}:
        return f"路径模板主机 {host} 未命中出口白名单"
    return None


def validate_declaration(decl: ConnectorDecl, *, has_secret: bool, timeout_max_ms: int) -> None:
    """注册校验(fail-fast 纯函数):畸形声明逐类拒绝,全部通过才落库。"""
    if not CONNECTOR_KEY_RE.fullmatch(decl.key):
        raise ConnectorValidationError("key 须为小写字母开头的字母/数字/下划线(2-64 位)")
    # 合成工具名 http__<key> 受 toolcall 执行契约 64 位硬闸(内部回调 422、
    # 审计列 VARCHAR(64))——注册期拦下,不产出必坏工具(评审修复)
    if len(tool_name(decl.key)) > 64:
        raise ConnectorValidationError(
            f"key 过长:合成工具名前缀 {CONNECTOR_TOOL_PREFIX} + key 超出 64 位,"
            f"key 上限 {64 - len(CONNECTOR_TOOL_PREFIX)} 位"
        )
    name = decl.name.strip()
    if not name:
        raise ConnectorValidationError("名称不能为空")
    if len(name) > NAME_MAX:
        raise ConnectorValidationError(f"名称超长(上限 {NAME_MAX})")
    if len(decl.description.strip()) == 0:
        raise ConnectorValidationError("描述不能为空(它是 LLM 判断何时调用该工具的依据)")
    if len(decl.description) > DESCRIPTION_MAX:
        raise ConnectorValidationError(f"描述超长(上限 {DESCRIPTION_MAX})")
    if decl.method not in METHODS:
        raise ConnectorValidationError(f"方法越白名单(仅允许 {'/'.join(METHODS)})")
    hosts_problem = _hosts_problem(decl.allowed_hosts)
    if hosts_problem:
        raise ConnectorValidationError(hosts_problem)
    ssrf_problem = _denied_host_problem(decl.allowed_hosts, decl.path_template)
    if ssrf_problem:
        raise ConnectorValidationError(ssrf_problem)
    template_problem = _template_problem(decl.path_template, decl.allowed_hosts, decl.params)
    if template_problem:
        raise ConnectorValidationError(template_problem)
    params_problem = _params_problem(decl.params)
    if params_problem:
        raise ConnectorValidationError(params_problem)
    if decl.auth_type not in AUTH_TYPES:
        raise ConnectorValidationError(f"鉴权形态越白名单(仅允许 {'/'.join(AUTH_TYPES)})")
    if decl.auth_type in SECRET_AUTH_TYPES and not has_secret:
        raise ConnectorValidationError(f"鉴权形态 {decl.auth_type} 必须提供凭证材料")
    if decl.auth_type == "none" and has_secret:
        raise ConnectorValidationError("鉴权形态 none 不允许携带凭证材料")
    # 运行期守卫:exact-type 检查(bool 是 int 子类,须显式排除;bool 穿透
    # 会得到 True≤上限 的恒真判定)
    if type(decl.timeout_ms) is not int or not TIMEOUT_MIN_MS <= decl.timeout_ms <= timeout_max_ms:
        raise ConnectorValidationError(f"超时须为 {TIMEOUT_MIN_MS}-{timeout_max_ms} 毫秒的整数")


def mask_secret(secret: str | None) -> str:
    """凭证掩码形态(纯函数):≥8 位出尾号 4 位,不足全星;空为空串。
    任何接口只出本形态,永不明文(spec「凭证不回显」)。"""
    if not secret:
        return ""
    return f"****{secret[-4:]}" if len(secret) >= 8 else "****"


def params_to_schema(params: tuple[ParamDecl, ...]) -> dict[str, Any]:
    """参数声明 → 工具参数 JSON Schema(注入素材;additionalProperties=false
    与既有工具同口径,策略器按声明校验是 schema 之外的纵深一层)。"""
    return {
        "type": "object",
        "properties": {
            p.name: {
                "type": _PARAM_TYPE_TO_SCHEMA[p.type],
                "description": p.description or p.name,
            }
            for p in params
        },
        "required": [p.name for p in params if p.required],
        "additionalProperties": False,
    }


def _row_params(row: dict[str, Any]) -> tuple[ParamDecl, ...]:
    """行内 params JSONB → 参数声明元组(畸形条目丢弃不炸装配面)。"""
    raw = row.get("params")
    if not isinstance(raw, list):
        return ()
    out: list[ParamDecl] = []
    for item in cast("list[object]", raw):
        d = cast("dict[str, object] | None", item if isinstance(item, dict) else None)
        if d is None:
            continue
        name = str(d.get("name") or "")
        ptype = str(d.get("type") or "")
        if not PARAM_NAME_RE.fullmatch(name) or ptype not in PARAM_TYPES:
            continue
        out.append(
            ParamDecl(
                name=name,
                type=ptype,
                required=bool(d.get("required")),
                description=str(d.get("description") or ""),
            )
        )
    return tuple(out)


def build_injection_spec(row: dict[str, Any]) -> dict[str, Any]:
    """连接器行 → Agent 注入声明(纯函数,同输入同输出;装配时机=每请求,
    注册/启停即时生效)。行数据须来自启用集(调用方保证)。"""
    params = _row_params(row)
    confirm = bool(row.get("requires_confirmation"))
    return {
        "name": tool_name(str(row["key"])),
        "description": str(row.get("description") or ""),
        "parameters": params_to_schema(params),
        "write": confirm,
        "require_confirm": confirm,
    }


def row_param_decls(row: dict[str, Any]) -> tuple[ParamDecl, ...]:
    """行 → 参数声明元组(策略器/执行器装配共用;公开薄壳)。"""
    return _row_params(row)
