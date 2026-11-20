"""评估器 fixtures：从 YAML 加载用例，经真实 /chat/query 生成结果，并直接执行 gold SQL。

需要已启动的 API、Agent、评估数据集和模型凭证；缺少凭证时整组评估明确跳过。
"""

import os
import urllib.parse
from pathlib import Path
from typing import Any, cast

import asyncpg
import httpx
import pytest
import yaml

EVAL_DIR = Path(__file__).parent
API_BASE = os.getenv("KM_EVAL_API_BASE", "http://127.0.0.1:8000")
ADMIN_USERNAME = os.getenv("KM_EVAL_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("KM_EVAL_ADMIN_PASSWORD", "")
# 评估内核选择(recipe 缺省 / react=ReAct 内核等价性验证)
AGENT_MODE = os.getenv("KM_EVAL_AGENT_MODE", "recipe")
CHAT_TIMEOUT = 150.0  # agent_timeout_sec=120 覆盖全图 + 网络余量

if not ADMIN_PASSWORD:
    pytest.skip("缺 KM_EVAL_ADMIN_PASSWORD(评估环境登录凭证);设置后重跑", allow_module_level=True)


# ============ 评估集加载 ============


def _load_yaml_cases() -> list[dict[str, Any]]:
    """加载 evals/ 下全部 yaml;单轮(无 turns)与多轮(turns[])统一返回,
    消费方按字段分流(文件即资产,不按文件名筛)。

    畸形结构(顶层非 list、条目非 dict)显式 raise——静默跳过会缩 full 统计
    分母,阈值口径悄悄失真。空文件(合法空 list)放行。
    """
    cases: list[dict[str, Any]] = []
    for yml in sorted(EVAL_DIR.glob("*.yaml")):
        with yml.open(encoding="utf-8") as f:
            # PyYAML 无 py.typed,safe_load 推断为 Unknown:先收口为 object 再走 isinstance 校验
            loaded = cast("object", yaml.safe_load(f) or [])
        if not isinstance(loaded, list):
            raise pytest.UsageError(f"{yml.name}: 顶层必须是题目列表(误写成单条 dict?)")
        # isinstance 对 object 收窄产物是 list[Unknown],逐条前显式 cast 定型
        for c in cast("list[object]", loaded):
            if not isinstance(c, dict):
                raise pytest.UsageError(f"{yml.name}: 条目必须是映射(误写了注释行?)")
            cases.append(cast("dict[str, Any]", c))
    return cases


def _tenant_of(case: dict[str, Any]) -> str:  # pyright: ignore[reportUnusedFunction]
    """租户:显式 tenant 字段优先,否则 id 前缀推断(sakila_001 → sakila)。
    供 test_eval 跨模块导入使用;pyright 不把跨文件引用计入访问,故行级豁免。"""
    return str(case.get("tenant") or case["id"].split("_")[0])


# ============ 登录与评估 API 调用 ============

_token_cache: str | None = None


async def _login() -> str:
    """账密登录拿 access JWT(自建认证,ADR-003;非上游 Keycloak 形态)。"""
    global _token_cache
    if _token_cache is not None:
        return _token_cache
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as c:
        r = await c.post(
            "/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
        )
    r.raise_for_status()
    _token_cache = cast("str", r.json()["access_token"])
    return _token_cache


async def call_chat(
    question: str,
    tenant: str,
    *,
    history: list[str] | None = None,
    datasource_key: str | None = None,
) -> httpx.Response:
    """调用户问答端点(persist=false 临时对话:不落会话/消息/不触发标题改写)。
    access TTL 15min,full+multiturn 可能超窗——401 由调用方触发重登重试。"""
    global _token_cache
    token = await _login()
    headers = {"Authorization": f"Bearer {token}"}
    if tenant != "default":
        headers["X-Tenant-Id"] = tenant
    body: dict[str, Any] = {"question": question, "persist": False}
    if history:
        body["history"] = history
    if datasource_key:
        body["datasource_key"] = datasource_key
    # AGENT_MODE=react 时使用 ReAct 图。
    # 默认 recipe 模式使用固定的 8 节点图。
    if AGENT_MODE != "recipe":
        body["agent_mode"] = AGENT_MODE
    async with httpx.AsyncClient(base_url=API_BASE, timeout=CHAT_TIMEOUT) as c:
        r = await c.post("/chat/query", headers=headers, json=body)
    if r.status_code == 401:  # token 过期:重登一次重发(上游同款)
        _token_cache = None
        token = await _login()
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(base_url=API_BASE, timeout=CHAT_TIMEOUT) as c:
            r = await c.post("/chat/query", headers=headers, json=body)
    return r


# ============ gold 直连执行 ============


def _pg_dsn(database: str | None = None) -> str:
    """gold 直连 DSN:KM_EVAL_PG_DSN 显式优先(独立 gold 库场景),缺省自
    控制面 DSN(settings)派生 → asyncpg 形态(剥 sqlalchemy +asyncpg 前缀)。
    database 非 None 时替换 URL 库段——动态数据源题 gold 直连其独立业务库
    (如 sakila_ext),与生成侧动态路由(search_path=default_schema)同一落点。"""
    url = os.getenv("KM_EVAL_PG_DSN") or _control_plane_dsn()
    dsn = url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    elif not dsn.startswith("postgresql://"):
        raise pytest.UsageError(f"不识别的 DSN 形态: {url[:40]}…")
    if database is not None:
        parts = urllib.parse.urlsplit(dsn)
        dsn = urllib.parse.urlunsplit(parts._replace(path=f"/{database}"))
    return dsn


def _control_plane_dsn() -> str:
    from kongming.config import get_settings

    return get_settings().database_url


async def exec_sql(
    sql: str, database: str | None = None
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """直连执行(gold_sql;asyncpg 原生类型,匹配器 canon_cell 归一化)。
    database=用例级 gold_database 字段(动态源独立库直连),None=控制面库。"""
    # asyncpg 无 py.typed:模块成员推断部分 Unknown,须在表达式级 cast("Any", …) 收口
    # (仅显式 Any 标注挡不住——赋值会把声明窄化回 Unknown)
    conn = await cast("Any", asyncpg).connect(_pg_dsn(database))
    try:
        stmt = await conn.prepare(sql)
        attrs = cast("list[Any]", stmt.get_attributes())
        cols = [str(a.name) for a in attrs]
        rows = cast("list[Any]", await stmt.fetch())
        return cols, [cast("tuple[Any, ...]", r) for r in rows]
    finally:
        await conn.close()


# ============ Recall@K(间接口径:生成 SQL 表覆盖 expected_tables) ============


def gen_tables(sql: str) -> set[str]:
    """生成 SQL 涉及表(sqlglot 抽表,CTE 排除——复用 feedback 实现)。"""
    from kongming.feedback.models import extract_table_names

    tables, _parse_error = extract_table_names(sql)
    return tables


# ============ pytest fixtures ============


@pytest.fixture(scope="session")
def all_cases() -> list[dict[str, Any]]:
    return _load_yaml_cases()
