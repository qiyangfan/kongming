"""datasource 领域模型与领域错误(008):纯 dataclass,零框架依赖(宪法 Article II)。

注册边界(datasource spec「数据源注册与凭证加密」):
- datasource_key 传输/领域同一 pattern(小写字母起头小写蛇形,2..64 位);
- 保留字 default 禁注册(内置默认路径标识,不登记为 sys_datasource 行);
- default_schema 须为合法 PG 未加引号标识符(小写折叠语义),防 search_path 注入;
- key 与租户归属创建后不可改(编辑须提交原值)。
"""

import re
from dataclasses import dataclass
from datetime import datetime

# 小写字母起头,小写字母/数字/下划线,总长 2..64(与列宽 VARCHAR(64) 一致)
DATASOURCE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
# PG 未加引号标识符(小写折叠语义),1..63 字节;search_path 注入防线第一道
PG_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

RESERVED_DATASOURCE_KEYS = ("default",)  # default=内置默认路径标识(平台配置连接串)

DatasourceStatus = str  # ACTIVE / DISABLED(baseline CHECK 同源字面量)


@dataclass(frozen=True)
class Datasource:
    """数据源聚合行(管理面视图;password_enc 永不进本模型——密文不出仓储)。"""

    id: str
    datasource_key: str
    name: str
    tenant_id: str
    tenant_key: str
    tenant_name: str
    host: str
    port: int
    database: str
    username: str
    default_schema: str
    readonly: bool
    status: str
    created_at: datetime


@dataclass(frozen=True)
class DatasourceOption:
    """选项清单项(对话页切换器;不含任何连接信息)。"""

    datasource_key: str
    name: str


@dataclass(frozen=True)
class ResolvedDatasource:
    """执行面解析结果(内部消费,含密文——仅限控制面内部,永不出 API 面)。

    status 随行返回:存在/启用/归属的分类判定在调用方(internal 路由编排),
    审计 reason 依赖分类(内部可溯,对外同形 400)。
    """

    datasource_key: str
    tenant_id: str
    host: str
    port: int
    database: str
    username: str
    password_enc: str
    default_schema: str
    status: str


@dataclass(frozen=True)
class TestConnectionResult:
    ok: bool
    reason: str | None = None


def validate_key(datasource_key: str) -> None:
    """注册/编辑边界:key pattern + 保留字双闸(spec「保留字与非法 key 拒绝」→ 400)。

    fullmatch 而非 match:Python `$` 匹配尾换行前位置,`"default\\n"` 可绕过
    match 版 pattern 与保留字集合双闸(R1-S2 实证)。
    """
    if not DATASOURCE_KEY_PATTERN.fullmatch(datasource_key):
        raise InvalidDatasourceKey("数据源键须为小写蛇形(小写字母起头,2..64 位)")
    if datasource_key in RESERVED_DATASOURCE_KEYS:
        raise InvalidDatasourceKey(f"{datasource_key} 为保留字,不可注册")


def validate_default_schema(default_schema: str) -> None:
    """default_schema 标识符合法性(spec「default_schema 注入防护」→ 400;fullmatch 同上)。"""
    if not PG_IDENTIFIER_PATTERN.fullmatch(default_schema):
        raise InvalidDefaultSchema("默认 schema 须为合法 PG 标识符(小写字母/下划线起头)")


# ---- 领域错误(router 统一映射状态码;message 即用户可见文案) ----


class DatasourceError(Exception):
    """datasource 域错误基类。"""


# 404


class DatasourceNotFound(DatasourceError):
    """数据源不存在。"""


# 409


class DatasourceKeyTaken(DatasourceError):
    """datasource_key 已存在(含禁用记录,硬 UNIQUE 同语义)。"""


# 400


class InvalidDatasourceKey(DatasourceError):
    """datasource_key 不匹配 pattern 或命中保留字。"""


class InvalidDefaultSchema(DatasourceError):
    """default_schema 非合法 PG 标识符。"""


class DatasourceKeyImmutable(DatasourceError):
    """datasource_key 创建后不可修改。"""


class DatasourceTenantImmutable(DatasourceError):
    """数据源租户归属创建后不可修改。"""


class UnknownTenant(DatasourceError):
    """租户归属指定的租户不存在或已禁用。"""


class CredentialUndecryptable(DatasourceError):
    """凭证密文不可解(密钥轮换/损坏)——不暴露密文与密钥细节。"""
