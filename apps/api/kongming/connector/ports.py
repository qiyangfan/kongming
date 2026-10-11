"""connector 端口(099):存储/加密/护栏/审计/出站反转,用例零框架依赖。"""

from typing import Any, Protocol


class ConnectorRepo(Protocol):
    """连接器存储(基础设施反转;行=数据库行 dict)。"""

    async def create(self, row: dict[str, Any]) -> str: ...

    async def update(
        self, *, connector_id: str, tenant_id: str, fields: dict[str, Any]
    ) -> bool: ...

    async def set_enabled(self, *, connector_id: str, tenant_id: str, enabled: str) -> bool: ...

    async def delete(self, *, connector_id: str, tenant_id: str) -> bool: ...

    async def get_masked(self, *, connector_id: str, tenant_id: str) -> dict[str, Any] | None: ...

    async def list_masked(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def list_enabled(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def find_enabled_by_key(self, *, tenant_id: str, key: str) -> dict[str, Any] | None: ...

    async def get_call_target(self, *, tenant_id: str, key: str) -> dict[str, Any] | None: ...

    async def commit(self) -> None: ...


class SecretCipher(Protocol):
    """凭证加解密(008 cipher 反转;与 webhook secret 同一单点机制)。"""

    def encrypt(self, plain: str) -> str: ...

    def decrypt(self, encoded: str) -> str: ...


class InputGuardrail(Protocol):
    """输入护栏(020 注册闸门:名称/描述是 LLM 可见注入面,与 bot-store 上架
    护栏同闸;BLOCK 拒注册/拒注入)。返回 (是否拦截, 处理后文本)。"""

    async def check(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]: ...


class ToolAuditor(Protocol):
    """工具审计(测试调用落既有工具审计表;实现=toolcall ToolAuditWriter)。"""

    async def record_test_call(
        self,
        *,
        tool_name: str,
        status: str,
        error_reason: str | None,
        duration_ms: int,
        tenant_id: str,
        user_id: str,
        args_digest: str,
    ) -> None: ...


class ConnectorCaller(Protocol):
    """出站调用反转(测试调用用例;实现=组合根按声明三件套走网关切面)。
    返回 (ok, status_code, 摘要文本, 失败分类文案)。"""

    async def call(
        self, *, row: dict[str, Any], secret: str | None, args: dict[str, object]
    ) -> tuple[bool, int | None, str, str | None]: ...


class OperLogger(Protocol):
    """操作审计端口(015 operlog 适配;CRUD/启停/删除留痕)。"""

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None: ...
