"""数据源注册、编辑、启停、删除、连接测试和选项查询用例。持久化、加解密、连接池失效和连接测试都通过端口注入；写操作先校验再落库，并在同一事务记录操作日志。连接池仅在数据库提交后失效。"""

import logging
from dataclasses import dataclass

from kongming.datasource.models import (
    CredentialUndecryptable,
    Datasource,
    DatasourceKeyImmutable,
    DatasourceKeyTaken,
    DatasourceNotFound,
    DatasourceOption,
    DatasourceTenantImmutable,
    TestConnectionResult,
    UnknownTenant,
    validate_default_schema,
    validate_key,
)
from kongming.datasource.ports import (
    ConnectionTester,
    CredentialCipher,
    DatasourceRepo,
    PoolEvictor,
    RagCtxEvictor,
    SemanticCleaner,
    SuggestionCacheEvictor,
)
from kongming.uuid7 import uuid7

log = logging.getLogger(__name__)

# 选项接口首位合成项:default=内置默认路径标识,非 sys_datasource 行
DEFAULT_OPTION = DatasourceOption(datasource_key="default", name="默认数据源")


class OptionsTenantForbidden(Exception):
    """options 租户解析失败(未绑定/不存在/禁用——同形不区分,与 chat 同构)。"""


@dataclass(frozen=True)
class _UpdateRow:
    """编辑落库所需全部字段(落库、建议缓存失效、操作日志与连接池失效共用)。"""

    actor_id: str
    ds_id: str
    datasource_key: str
    tenant_id: str
    name: str
    host: str
    port: int
    database: str
    username: str
    password: str | None
    default_schema: str
    readonly: bool


class DatasourceService:
    def __init__(
        self,
        repo: DatasourceRepo,
        cipher: CredentialCipher,
        evictor: PoolEvictor,
        tester: ConnectionTester,
    ) -> None:
        self._repo = repo
        self._cipher = cipher
        self._evictor = evictor
        self._tester = tester

    # ---- 分页(全局视角:X-Tenant-Id 无语义,与身份治理同惯例) ----

    async def page(self, *, page: int, size: int, keyword: str) -> tuple[int, list[Datasource]]:
        kw = keyword.strip()
        total = await self._repo.count(kw)
        items = await self._repo.list_page(kw, size, (page - 1) * size)
        return total, items

    # ---- 创建 ----

    async def create(
        self,
        *,
        actor_id: str,
        datasource_key: str,
        name: str,
        tenant_id: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
        default_schema: str,
        readonly: bool,
    ) -> str:
        validate_key(datasource_key)  # pattern + 保留字(400 口径,spec 立约)
        validate_default_schema(default_schema)
        if not await self._repo.tenant_active(tenant_id):
            raise UnknownTenant("租户不存在或已禁用")
        if await self._repo.key_taken(datasource_key):
            raise DatasourceKeyTaken(f"数据源键 {datasource_key} 已存在")
        ds_id = str(uuid7())
        await self._repo.insert(
            ds_id=ds_id,
            datasource_key=datasource_key,
            name=name,
            tenant_id=tenant_id,
            host=host,
            port=port,
            database=database,
            username=username,
            password_enc=self._cipher.encrypt(password),
            default_schema=default_schema,
            readonly=readonly,
        )
        await self._repo.log_oper(
            actor_id,
            "datasource.create",
            datasource_key,
            {"datasource_id": ds_id, "tenant_id": tenant_id},
        )
        await self._repo.commit()
        return ds_id

    # ---- 编辑(全量表单语义;key/租户归属不可改;密码留空=不改) ----

    async def update(
        self,
        *,
        actor_id: str,
        ds_id: str,
        datasource_key: str,
        tenant_id: str,
        name: str,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str | None,
        default_schema: str,
        readonly: bool,
        suggestion_evictor: SuggestionCacheEvictor | None = None,
        ctx_evictor: RagCtxEvictor | None = None,
    ) -> None:
        validate_default_schema(default_schema)
        await self._write_update(
            _UpdateRow(
                actor_id=actor_id,
                ds_id=ds_id,
                datasource_key=datasource_key,
                tenant_id=tenant_id,
                name=name,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                default_schema=default_schema,
                readonly=readonly,
            ),
            suggestion_evictor=suggestion_evictor,
            ctx_evictor=ctx_evictor,
        )

    async def _write_update(
        self,
        fields: _UpdateRow,
        *,
        suggestion_evictor: SuggestionCacheEvictor | None,
        ctx_evictor: RagCtxEvictor | None,
    ) -> None:
        """落库编辑字段并连带建议/检索缓存失效、操作日志与提交;
        连接变化后再失效连接池。"""
        current = await self._editable_row(
            fields.ds_id, datasource_key=fields.datasource_key, tenant_id=fields.tenant_id
        )
        # 连接信息变更检测:host/port/database/username/password/
        # default_schema 任一变化 → commit 后定向 evict;仅改 name 等展示字段不动池
        conn_changed = (
            current.host != fields.host
            or current.port != fields.port
            or current.database != fields.database
            or current.username != fields.username
            or current.default_schema != fields.default_schema
            or bool(fields.password)
        )
        await self._repo.update(
            fields.ds_id,
            name=fields.name,
            host=fields.host,
            port=fields.port,
            database=fields.database,
            username=fields.username,
            password_enc=self._cipher.encrypt(fields.password) if fields.password else None,
            default_schema=fields.default_schema,
            readonly=fields.readonly,
        )
        # 连接信息变更级联失效建议缓存(同事务;仅改 name 不失效)
        if conn_changed and suggestion_evictor is not None:
            await suggestion_evictor.evict_by_datasource(fields.datasource_key)
        await self._repo.log_oper(
            fields.actor_id,
            "datasource.update",
            fields.datasource_key,
            {"datasource_id": fields.ds_id},
        )
        await self._repo.commit()
        if conn_changed:
            await self._evict_quietly(fields.datasource_key)
            if ctx_evictor is not None:
                try:
                    # 连接变化后旧 schema_hint/图不可继续使用;失效失败按
                    # 缓存策略降级,不阻断已提交的数据源编辑。
                    await ctx_evictor(fields.tenant_id, fields.datasource_key)
                except Exception:
                    log.warning(
                        "datasource 更新后的 rag 缓存失效失败 key=%s",
                        fields.datasource_key,
                        exc_info=True,
                    )

    async def _editable_row(self, ds_id: str, *, datasource_key: str, tenant_id: str) -> Datasource:
        """取待编辑行并校验存在、key 与租户归属未变;任一不符按对应领域异常拒绝。"""
        current = await self._repo.get(ds_id)
        if current is None:
            raise DatasourceNotFound("数据源不存在")
        if current.datasource_key != datasource_key:
            raise DatasourceKeyImmutable("datasource_key 创建后不可修改")
        if current.tenant_id != tenant_id:
            raise DatasourceTenantImmutable("数据源租户归属创建后不可修改")
        return current

    # ---- 启停 ----

    async def set_status(self, *, actor_id: str, ds_id: str, active: bool) -> None:
        current = await self._repo.get(ds_id)
        if current is None:
            raise DatasourceNotFound("数据源不存在")
        status = "ACTIVE" if active else "DISABLED"
        await self._repo.update_status(ds_id, status)
        action = "datasource.enable" if active else "datasource.disable"
        await self._repo.log_oper(
            actor_id, action, current.datasource_key, {"datasource_id": ds_id}
        )
        await self._repo.commit()
        # 启停均定向失效:禁用即拒(防存活池兜底),启用后按最新连接信息惰性重建
        await self._evict_quietly(current.datasource_key)

    # ---- 删除(物理删) ----

    async def delete(
        self,
        *,
        actor_id: str,
        ds_id: str,
        ctx_evictor: RagCtxEvictor | None = None,
        semantic_cleaner: "SemanticCleaner | None" = None,
        suggestion_evictor: SuggestionCacheEvictor | None = None,
    ) -> None:
        current = await self._repo.get(ds_id)
        if current is None:
            raise DatasourceNotFound("数据源不存在")
        await self._repo.delete(ds_id)
        # 语义层级联清理(关系+指标,同事务;spec「数据源删除级联」)
        if semantic_cleaner is not None:
            await semantic_cleaner.delete_by_datasource(
                tenant_id=current.tenant_id, datasource_key=current.datasource_key
            )
        # 建议缓存级联失效(同事务,不留孤儿行)
        if suggestion_evictor is not None:
            await suggestion_evictor.evict_by_datasource(current.datasource_key)
        await self._repo.log_oper(
            actor_id, "datasource.delete", current.datasource_key, {"datasource_id": ds_id}
        )
        await self._repo.commit()
        await self._evict_quietly(current.datasource_key)
        # 数据源删除同步失效 rag 检索缓存(残留知识条目不再可召回;
        # 知识条目本身保留,级联清理策略另行决策)
        if ctx_evictor is not None:
            try:
                await ctx_evictor(current.tenant_id, current.datasource_key)
            except Exception:  # 缓存失效失败不阻断删除
                log.warning("datasource 删除的 rag ctx 失效失败", exc_info=True)

    # ---- 测试连接(无状态:不落库/不建池/不落审计) ----

    async def test_connection(
        self,
        *,
        ds_id: str | None,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str | None,
    ) -> TestConnectionResult:
        plain = password
        if not plain:
            # 编辑态密码留空:取库存密文解密补位(密文不回显;不可解归「凭证不可用」)
            if ds_id is None:
                return TestConnectionResult(ok=False, reason="请填写密码")
            enc = await self._repo.get_password_enc(ds_id)
            if enc is None:
                raise DatasourceNotFound("数据源不存在")
            try:
                plain = self._cipher.decrypt(enc)
            except CredentialUndecryptable:
                return TestConnectionResult(ok=False, reason="凭证不可用:请重新填写密码后保存再试")
        return await self._tester.test(
            host=host, port=port, database=database, username=username, password=plain
        )

    # ---- 选项(登录即可的用户面接口;首位合成 default 项) ----

    async def options(self, *, user_id: str, tenant_key: str | None) -> list[DatasourceOption]:
        tenant_id = await self._resolve_tenant(user_id, tenant_key)
        rows = await self._repo.list_enabled_by_tenant(tenant_id)
        return [DEFAULT_OPTION, *rows]

    async def _resolve_tenant(self, user_id: str, tenant_key: str | None) -> str:
        """X-Tenant-Id 解析与 chat 同构:显式传入查白名单;缺省取默认租户。"""
        if tenant_key is not None:
            tenant_id = await self._repo.find_bound_tenant(user_id, tenant_key)
            if tenant_id is None:
                raise OptionsTenantForbidden(tenant_key)
            return tenant_id
        default = await self._repo.find_default_tenant(user_id)
        if default is None:
            raise OptionsTenantForbidden("<default>")
        return default[0]

    # ---- 池定向失效(commit 后;尽力而为,失败仅告警不阻断) ----

    async def _evict_quietly(self, datasource_key: str) -> None:
        try:
            await self._evictor.evict(datasource_key)
        except Exception:
            # 旧池残留窗口随目标库凭证轮换自然收敛;多 worker 跨进程失效无统一方案
            log.warning("连接池定向失效失败 key=%s", datasource_key, exc_info=True)
