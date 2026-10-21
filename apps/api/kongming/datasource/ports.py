"""datasource 域端口,以 typing.Protocol 隔离用例层与存储实现。

service 只依赖本接口;SQLAlchemy/cryptography/asyncpg/executor 实现各自落在
repository.py / cipher.py / tester.py / router 装配层。
事务由 repo 自身的 commit/rollback 收口,service 不触碰 Session(零框架)。
"""

from __future__ import annotations

from typing import Protocol

from kongming.datasource.models import (
    Datasource,
    DatasourceOption,
    ResolvedDatasource,
    TestConnectionResult,
)


class DatasourceRepo(Protocol):
    # ---- 管理面 ----

    async def count(self, keyword: str) -> int: ...
    async def list_page(self, keyword: str, limit: int, offset: int) -> list[Datasource]: ...
    async def get(self, ds_id: str) -> Datasource | None: ...
    async def get_password_enc(self, ds_id: str) -> str | None:
        """取库存密文(测试连接编辑态补位唯一消费点;不存在返回 None)。"""
        ...

    async def key_taken(self, datasource_key: str) -> bool:
        """全局唯一(含禁用记录,与硬 UNIQUE 同语义;物理删除后释放)。"""
        ...

    async def tenant_active(self, tenant_id: str) -> bool:
        """创建时租户归属校验:存在且 ACTIVE。"""
        ...

    async def insert(
        self,
        *,
        ds_id: str,
        datasource_key: str,
        name: str,
        tenant_id: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password_enc: str,
        default_schema: str,
        readonly: bool,
    ) -> None: ...
    async def update(
        self,
        ds_id: str,
        *,
        name: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password_enc: str | None,
        default_schema: str,
        readonly: bool,
    ) -> None:
        """全量字段覆盖(表单全量语义);password_enc None=不改(专用语句不触碰该列)。"""
        ...

    async def update_status(self, ds_id: str, status: str) -> None: ...
    async def delete(self, ds_id: str) -> None:
        """物理删除(软删仅 identity 域,AGENTS.md 约定)。"""
        ...

    # ---- 执行面(internal 路由/meta/chat 预检消费) ----

    async def find_for_execute(self, datasource_key: str) -> ResolvedDatasource | None:
        """按 key 取全字段行(含密文——仅限控制面内部);不存在返回 None。"""
        ...

    # ---- 选项接口(租户复校自备,不跨域 import chat) ----

    async def find_bound_tenant(self, user_id: str, tenant_key: str) -> str | None:
        """用户绑定白名单复校:命中返回 tenant_id,否则 None(与 chat 同语义)。"""
        ...

    async def find_default_tenant(self, user_id: str) -> tuple[str, str] | None:
        """缺省租户(default 优先,否则最早授予);返回 (tenant_id, tenant_key)。"""
        ...

    async def list_enabled_by_tenant(self, tenant_id: str) -> list[DatasourceOption]: ...

    # 操作审计(与业务同事务,组合模式) ----

    async def log_oper(
        self, actor_id: str, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...

    # ---- 事务 ----

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class CredentialCipher(Protocol):
    """凭证加解密端口;解密失败抛 models.CredentialUndecryptable。"""

    def encrypt(self, plain: str) -> str: ...
    def decrypt(self, encoded: str) -> str: ...


class PoolEvictor(Protocol):
    """连接池定向失效端口(commit 后调用;尽力而为,失败仅告警不阻断)。"""

    async def evict(self, datasource_key: str) -> None: ...


class ConnectionTester(Protocol):
    """测试连接端口(asyncpg 直连;不落库不建池不落审计)。"""

    async def test(
        self, *, host: str, port: int, database: str, username: str, password: str
    ) -> TestConnectionResult: ...


class DatasourceChecker(Protocol):
    """chat 入口 fail-fast 预检端口:存在+启用+归属。"""

    async def is_usable(self, datasource_key: str, tenant_id: str) -> bool: ...


class SemanticCleaner(Protocol):
    """数据源删除级联清理语义层(关系+指标;同事务,spec 立约)。"""

    async def delete_by_datasource(self, *, tenant_id: str, datasource_key: str) -> None: ...


class RagCtxEvictor(Protocol):
    """rag 检索缓存失效窄端口(数据源删除时同步失效该源检索缓存)。"""

    async def __call__(self, tenant_id: str, datasource_key: str) -> None: ...


class SuggestionCacheEvictor(Protocol):
    """建议问题缓存失效窄端口(数据源连接信息变更/删除时级联失效
    该源全部租户的对话冷启动建议缓存;实现=chat 域仓储,管理事务内调用)。"""

    async def evict_by_datasource(self, datasource_key: str) -> None: ...
