"""内置工具注册表(019):代码声明式,随代码发布的契约数据(与权限点同哲学)。

唯一真理源纪律:Agent 面不维护独立工具 schema——控制面在调用 ReAct 端点时
按调用方授权集过滤本表注入(filter_specs);新增/变更工具只改本文件。
零框架纪律:本模块不 import fastapi/sqlalchemy/redis。

部署级可选内置工具(094 web-search-domain):REGISTRY 是静态基表(常驻工具,
零回归缺省);build_registry(settings 条件) 产出有效表,启动时 activate。
条件不满足的工具不进有效表——对调用方与 Agent 面「从未存在」(spec
「部署级注册条件」)。
"""

from collections.abc import Collection

from kongming.toolcall.models import ToolDef, ToolSpec
from kongming.toolgateway.models import (
    WEBSEARCH_COUNT_DEFAULT,
    WEBSEARCH_COUNT_MAX,
    WEBSEARCH_COUNT_MIN,
    WEBSEARCH_PROVIDERS,
    WEBSEARCH_QUERY_MAX_LEN,
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

# 089:运行时停用工具(网关域停用 → 注入集与分发解析同源消失,与「域从未存在」
# 同形;进程内生效,重启随部署清单复位)
_RUNTIME_DISABLED: set[str] = set()


def set_runtime_disabled(tool_names: frozenset[str]) -> None:
    """替换运行时停用集内容(治理面启停端点调用;非增量;容器清空重填)。"""
    _RUNTIME_DISABLED.clear()
    _RUNTIME_DISABLED.update(tool_names)


def build_registry(
    *,
    websearch_provider: str = "",
    websearch_api_key: str = "",
) -> dict[str, ToolDef]:
    """094:有效注册表装配(确定性纯函数:同入参同结果)。

    websearch 供应商在注册表内且 API key 非空才追加 web_search;
    调用方(main.lifespan)负责配置残缺告警,本函数只做条件判定。
    """
    registry = dict(REGISTRY)
    provider = (websearch_provider or "").strip().lower()
    if provider in WEBSEARCH_PROVIDERS and (websearch_api_key or "").strip():
        registry[_WEBSEARCH_TOOL.name] = _WEBSEARCH_TOOL
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
