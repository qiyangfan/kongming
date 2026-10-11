"""内置工具注册表(019):代码声明式,随代码发布的契约数据(与权限点同哲学)。

唯一真理源纪律:Agent 面不维护独立工具 schema——控制面在调用 ReAct 端点时
按调用方授权集过滤本表注入(filter_specs);新增/变更工具只改本文件。
零框架纪律:本模块不 import fastapi/sqlalchemy/redis。

部署级可选内置工具(081 external-knowledge):REGISTRY 是静态基表(常驻工具,
零回归缺省);build_registry(settings 条件) 产出有效表,启动时 activate。
条件不满足的工具不进有效表——对调用方与 Agent 面「从未存在」(spec
「部署级注册条件」)。
"""

import urllib.parse
from collections.abc import Collection

from kongming.toolcall.models import ToolDef, ToolSpec
from kongming.toolgateway.models import (
    TICKET_LIMIT_DEFAULT,
    TICKET_LIMIT_MAX,
    TICKET_LIMIT_MIN,
    TICKET_QUERY_MAX_LEN,
    TICKET_STATUS_MAX_LEN,
    WEBSEARCH_COUNT_DEFAULT,
    WEBSEARCH_COUNT_MAX,
    WEBSEARCH_COUNT_MIN,
    WEBSEARCH_PROVIDERS,
    WEBSEARCH_QUERY_MAX_LEN,
    parse_ticket_endpoint,
)

_SQL_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "minLength": 1, "description": "单条 SELECT 语句"},
    },
    "required": ["sql"],
    "additionalProperties": False,
}

# 注册表:name → ToolDef;handler 键与 name 同名(service 分发)
REGISTRY: dict[str, ToolDef] = {
    "validate_sql": ToolDef(
        name="validate_sql",
        description="校验一条 SQL 是否通过只读防火墙(AST 预检),执行前先校验可省执行配额",
        parameters=_SQL_PARAM_SCHEMA,
        perm_code="tool:sql:validate",
        write=False,
        require_confirm=False,
    ),
    "execute_sql": ToolDef(
        name="execute_sql",
        description="执行一条只读 SELECT 查询并返回结果集(经防火墙+行级数据权限+只读池)",
        parameters={
            "type": "object",
            "properties": {
                "sql": {"type": "string", "minLength": 1, "description": "单条 SELECT 语句"},
                "datasource_key": {
                    "type": "string",
                    "description": "目标数据源(缺省 default)",
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        perm_code="tool:sql:execute",
        write=False,
        require_confirm=False,
    ),
    "rag_retrieve": ToolDef(
        name="rag_retrieve",
        description="检索知识库:相关表结构说明与历史成功 SQL(生成 SQL 前先检索)",
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1, "description": "检索问句"},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        perm_code="tool:rag:retrieve",
        write=False,
        require_confirm=False,
    ),
    "create_system_announcement": ToolDef(
        name="create_system_announcement",
        description="发布一条系统公告(写操作,需用户确认后执行;幂等防重复发布)",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "content": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            "required": ["title", "content"],
            "additionalProperties": False,
        },
        perm_code="tool:announcement:create",
        write=True,
        require_confirm=True,
    ),
    # 027 cloud-code-sandbox:容器级隔离的 Python 执行面(非写工具——
    # 沙箱无副作用出边界);用法说明自带(df 注入语义/pd/np/plt 可用),
    # 不动 Agent 面 prompt 资产(prompt 资产纪律)
    "run_python_code": ToolDef(
        name="run_python_code",
        description=(
            "在隔离沙箱执行 Python 数据分析代码(pd/np/plt 已预装可直接用;"
            "stdout 打印的内容会回传)。需要数据时传 data_sql——服务端经只读"
            "防火墙+行级权限取数后以 df 变量(pandas DataFrame)注入,"
            "禁止把行数据逐行拼进 code;plt 画的末图自动收集"
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 8000,
                    "description": "Python 代码;可引用 df(data_sql 注入)/pd/np/plt",
                },
                "data_sql": {
                    "type": "string",
                    "description": "可选:单条只读 SELECT,结果注入 df 变量(≤1000 行)",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        perm_code="tool:code:execute",
        write=False,
        require_confirm=False,
    ),
    # 057 numeric-grounding:确定性派生量(禁 LLM 心算);Agent 面本地执行并
    # 校验入参 ∈ 本轮数值白名单;控制面 handler 供工作流等通道复用同公式
    "compute_delta": ToolDef(
        name="compute_delta",
        description=(
            "计算增幅百分比 (target-base)/|base|*100。入参必须是本轮查询结果"
            "或统计中已出现的数字;返回 delta_pct 与入参快照,结果可写入结论"
        ),
        parameters={
            "type": "object",
            "properties": {
                "base": {"type": "number", "description": "基期数值(须来自结果/统计)"},
                "target": {"type": "number", "description": "本期数值(须来自结果/统计)"},
            },
            "required": ["base", "target"],
            "additionalProperties": False,
        },
        perm_code="tool:compute",
        write=False,
        require_confirm=False,
    ),
    "compute_share": ToolDef(
        name="compute_share",
        description=(
            "计算占比 part/total(返回小数 share 与 share_pct)。入参必须是本轮"
            "查询结果或统计中已出现的数字;结果可写入结论"
        ),
        parameters={
            "type": "object",
            "properties": {
                "part": {"type": "number", "description": "部分值(须来自结果/统计)"},
                "total": {"type": "number", "description": "总体值(须来自结果/统计)"},
            },
            "required": ["part", "total"],
            "additionalProperties": False,
        },
        perm_code="tool:compute",
        write=False,
        require_confirm=False,
    ),
}


# 081 external-knowledge:个人知识库检索工具(部署级可选;注册条件两道:
# 端点已配置 + localhost 白名单;多租户仅告警不拒绝,见 main.lifespan,082)
_KNOWLEDGE_TOOL = ToolDef(
    name="knowledge_search",
    description=(
        "检索个人本地知识库(siku)中的文档:业务口径、规范、笔记等非结构化"
        "资料。数据库里查得到的结构性数据不要用它;返回命中文本与来源"
        "锚点(note_path/anchor),答案引用时须带上锚点"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": "检索问句(自然语言)",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "返回命中条数(缺省 5)",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    perm_code="tool:knowledge:search",
    write=False,
    require_confirm=False,
)

# localhost 等价主机集(fail-closed 白名单;host.docker.internal 是 compose
# 部署形态下容器到宿主机的 localhost 等价形式,design D3)
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "host.docker.internal", "::1", "[::1]"})
LOCAL_HOSTS = _LOCAL_HOSTS  # 组合根白名单单源复用(089 域清单)

# 094 web-search-domain:联网检索工具(部署级可选;注册条件两道:
# 供应商在注册表内 + API key 已配置;查询内容外发第三方,显式授予制)
_WEBSEARCH_TOOL = ToolDef(
    name="web_search",
    description=(
        "联网搜索公开网页:时效性问题、数据库与个人知识库均无据的问题先用它,"
        "再综合作答。返回检索卡(标题/链接/摘要),引用时须带链接;"
        "站内结构性数据查本地数据源,不要用它"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": WEBSEARCH_QUERY_MAX_LEN,
                "description": "搜索问句(自然语言)",
            },
            "count": {
                "type": "integer",
                "minimum": WEBSEARCH_COUNT_MIN,
                "maximum": WEBSEARCH_COUNT_MAX,
                "description": f"返回检索卡条数(缺省 {WEBSEARCH_COUNT_DEFAULT})",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    perm_code="tool:websearch:search",
    write=False,
    require_confirm=False,
)

# 100 internal-connector-domain:内部工单三工具(部署级可选;注册条件两道:
# 端点与服务凭证均配置,端点解析与网关清单声明同源 parse_ticket_endpoint)。
# 读/写分权:search/get 同 tool:ticket:search(读面放宽),create 走
# tool:ticket:manage(写面收紧)+ 缺省需确认(确认流独立于权限点,design D4)。
# 状态/优先级枚举语义只在描述层说明(prompt 层翻译,design D5),代码不翻译。
_TICKET_SEARCH_TOOL = ToolDef(
    name="ticket_search",
    description=(
        "检索内部工单系统的工单:按关键词(匹配标题/描述)与状态过滤,返回"
        "工单卡(标识/标题/状态/优先级/负责人/时间)与命中计数。查询工单"
        "进度、找历史工单时先用它。status 取值原样透传工单系统(常用:"
        "open=待处理,in_progress=处理中,resolved=已解决,closed=已关闭),"
        "不确定时可先不带状态检索"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "maxLength": TICKET_QUERY_MAX_LEN,
                "description": "关键词(匹配标题或描述;省略=返回全部/最近工单)",
            },
            "status": {
                "type": "string",
                "minLength": 1,
                "maxLength": TICKET_STATUS_MAX_LEN,
                "description": "状态过滤(原样透传,如 open/in_progress/resolved/closed)",
            },
            "limit": {
                "type": "integer",
                "minimum": TICKET_LIMIT_MIN,
                "maximum": TICKET_LIMIT_MAX,
                "description": f"返回条数上限(缺省 {TICKET_LIMIT_DEFAULT})",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
    perm_code="tool:ticket:search",
    write=False,
    require_confirm=False,
)

_TICKET_GET_TOOL = ToolDef(
    name="ticket_get",
    description=(
        "按标识查看单个工单详情(标准化工单卡:标识/标题/状态/优先级/负责人/"
        "时间)。标识来自 ticket_search 的结果"
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
                "description": "工单标识(如 T-1001)",
            },
        },
        "required": ["id"],
        "additionalProperties": False,
    },
    perm_code="tool:ticket:search",
    write=False,
    require_confirm=False,
)

_TICKET_CREATE_TOOL = ToolDef(
    name="ticket_create",
    description=(
        "在内部工单系统创建工单(写操作,需用户确认后执行;创建前向用户复述"
        "标题与优先级)。priority 取值原样透传(常用:high=高,medium=中,"
        "low=低);assignee 为负责人姓名,可省略"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": {"type": "string", "minLength": 1, "maxLength": 2000},
            "priority": {
                "type": "string",
                "minLength": 1,
                "maxLength": TICKET_STATUS_MAX_LEN,
                "description": "优先级(原样透传:high/medium/low)",
            },
            "assignee": {
                "type": "string",
                "maxLength": 64,
                "description": "负责人姓名(可省略)",
            },
        },
        "required": ["title", "description", "priority"],
        "additionalProperties": False,
    },
    perm_code="tool:ticket:manage",
    write=True,
    require_confirm=True,
)

_TICKET_TOOLS: tuple[ToolDef, ...] = (
    _TICKET_SEARCH_TOOL,
    _TICKET_GET_TOOL,
    _TICKET_CREATE_TOOL,
)

# 089:运行时停用工具(网关域停用 → 注入集与分发解析同源消失,与「域从未存在」
# 同形;进程内生效,重启随部署清单复位)
_RUNTIME_DISABLED: set[str] = set()


def set_runtime_disabled(tool_names: frozenset[str]) -> None:
    """替换运行时停用集内容(治理面启停端点调用;非增量;容器清空重填)。"""
    _RUNTIME_DISABLED.clear()
    _RUNTIME_DISABLED.update(tool_names)


def is_local_base_url(url: str) -> bool:
    """081:端点主机是否在 localhost 白名单(纯函数;畸形 URL/非 http(s) 一律 False)。"""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in _LOCAL_HOSTS


def build_registry(
    knowledge_base_url: str | None,
    *,
    websearch_provider: str = "",
    websearch_api_key: str = "",
    ticket_base_url: str = "",
    ticket_api_key: str = "",
) -> dict[str, ToolDef]:
    """081/094/100:有效注册表装配(确定性纯函数:同入参同结果)。

    knowledge_base_url 非空且过 localhost 白名单才追加 knowledge_search;
    websearch 供应商在注册表内且 API key 非空才追加 web_search;
    ticket 端点形态合法且服务凭证非空才追加工单三工具(端点解析与网关
    清单声明同源 parse_ticket_endpoint,不出现「注入了却执行必拒」);
    调用方(main.lifespan)负责多租户告警与配置残缺告警,本函数只做条件判定。
    """
    registry = dict(REGISTRY)
    if knowledge_base_url and is_local_base_url(knowledge_base_url):
        registry[_KNOWLEDGE_TOOL.name] = _KNOWLEDGE_TOOL
    provider = (websearch_provider or "").strip().lower()
    if provider in WEBSEARCH_PROVIDERS and (websearch_api_key or "").strip():
        registry[_WEBSEARCH_TOOL.name] = _WEBSEARCH_TOOL
    if parse_ticket_endpoint(ticket_base_url) and (ticket_api_key or "").strip():
        for t in _TICKET_TOOLS:
            registry[t.name] = t
    return registry


# 有效表:缺省=静态基表(未 activate 的测试/导入路径零回归);activate 后
# filter_specs/get_tool 及其余直接读表点经 effective() 消费
_active_registry: dict[str, ToolDef] = REGISTRY


def activate(registry: dict[str, ToolDef]) -> None:
    """启动装配入口(main.lifespan 调用一次;测试可 activate 后还原)。"""
    global _active_registry  # noqa: PLW0603 —— 进程级单次装配,组合根模式
    _active_registry = registry


def effective() -> dict[str, ToolDef]:
    return _active_registry


def filter_specs(perm_codes: Collection[str]) -> list[ToolSpec]:
    """按调用方权限点集过滤出可注入 Agent 面的工具 schema(空集→空列表,退化语义);
    089:运行时停用工具不出现在注入集(与「域从未存在」同形)。"""
    return [
        d.spec()
        for d in _active_registry.values()
        if d.perm_code in perm_codes and d.name not in _RUNTIME_DISABLED
    ]


def get_tool(name: str) -> ToolDef | None:
    if name in _RUNTIME_DISABLED:
        return None  # 停用工具解析即拒(TOOL_NOT_REGISTERED 形,零感知)
    return _active_registry.get(name)
