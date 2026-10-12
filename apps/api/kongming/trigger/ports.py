"""trigger 端口(031):存储与目标执行反转,用例/调度零框架依赖。"""

from dataclasses import dataclass
from typing import Any, Protocol


class TriggerRepo(Protocol):
    async def create(self, **kw: Any) -> str: ...

    async def update(self, **kw: Any) -> bool: ...

    async def get(self, *, tenant_id: str, tg_key: str) -> dict[str, Any] | None: ...

    async def get_by_id(self, *, trigger_id: str) -> dict[str, Any] | None: ...

    async def list_all(self, *, tenant_id: str) -> list[dict[str, Any]]: ...

    async def set_enabled(self, *, trigger_id: str, tenant_id: str, enabled: bool) -> bool: ...

    async def delete(self, *, trigger_id: str, tenant_id: str) -> bool: ...

    async def find_by_webhook_path(self, *, path: str) -> dict[str, Any] | None: ...

    async def list_due_cron(self, *, now_iso: str) -> list[dict[str, Any]]: ...

    async def mark_fired(
        self, *, trigger_id: str, last_fired_iso: str, next_fire_iso: str | None
    ) -> None: ...

    async def insert_run(
        self,
        *,
        tenant_id: str,
        trigger_id: str,
        status: str,
        error: str,
        duration_ms: int,
        target_run_ref: str,
        attempt: int = 1,
        retry_of_run_id: str | None = None,
        next_retry_at: Any | None = None,
        summary: str = "",
    ) -> str: ...

    async def get_run(self, *, run_id: str) -> dict[str, Any] | None: ...

    async def list_due_retries(self, *, now_iso: str) -> list[dict[str, Any]]: ...

    async def list_due_emails(self, *, now_iso: str) -> list[dict[str, Any]]: ...

    async def get_email_cursor(self, *, trigger_id: str, folder: str) -> dict[str, Any] | None: ...

    async def upsert_email_cursor(
        self, *, trigger_id: str, folder: str, last_uid: int, uidvalidity: int
    ) -> None: ...

    async def update_run_terminal(
        self,
        *,
        run_id: str,
        status: str,
        error: str,
        duration_ms: int,
        target_run_ref: str,
    ) -> int: ...

    async def defer_retry(self, *, run_id: str, next_retry_at: Any) -> None: ...

    async def cancel_pending_retries(self, *, trigger_id: str, reason: str) -> None: ...

    async def page_runs(
        self, *, tenant_id: str, trigger_id: str, page: int, size: int
    ) -> tuple[int, list[dict[str, Any]]]: ...

    async def commit(self) -> None: ...


class TargetRunner(Protocol):
    """目标执行(031):实现侧装配 029/025 组合根(独立会话);
    返回 (success, ref, error)。"""

    async def run_target(
        self,
        *,
        target_type: str,
        target_key: str,
        tenant_id: str,
        tenant_key: str,
        creator_id: str,
        inputs: dict[str, Any],
        channel: str,
    ) -> tuple[bool, str, str]: ...


class TargetChecker(Protocol):
    """目标存在性(040):创建/改指时写边界 fail-fast。

    workflow=创建者视角可见集合(与前端候选同源,与派发期 032 可见性同口径);
    bot=租户级存在(与派发期 get_by_key 同口径)。实现见 router 组合根。
    """

    async def target_exists(
        self, *, target_type: str, target_key: str, tenant_id: str, creator_id: str
    ) -> bool: ...


class SecretCipher(Protocol):
    """密钥加解密(008 cipher 反转)。"""

    def encrypt(self, plain: str) -> str: ...

    def decrypt(self, encoded: str) -> str: ...


class Notifier(Protocol):
    """出站通知(031):实现侧 httpx+SSRF 判定(029 拒绝段复用);
    check_url 在 post 前由 service 调用。"""

    async def check_url(self, url: str) -> None: ...

    async def post(self, *, url: str, body: bytes, signature: str) -> None: ...


# ── 邮件入站(108) ──


class MailError(Exception):
    """IMAP 邮件栈错误基类(轮询失败按子类分类落 run 历史)。"""


class MailConnectError(MailError):
    """连接失败(网络不可达/TLS 握手/超时)。"""


class MailAuthError(MailError):
    """认证失败(账号/口令)。"""


class MailProtocolError(MailError):
    """协议失败(文件夹不可选/UID 查询/拉取异常)。"""


@dataclass(frozen=True)
class MailRawMessage:
    """单封原始邮件(RFC 822 字节;解析与四字段白名单在领域适配器)。"""

    uid: int
    data: bytes


@dataclass(frozen=True)
class MailFetch:
    """一次拉取结果:文件夹 UIDVALIDITY + 游标后的原始邮件(升序)。"""

    uidvalidity: int
    messages: list[MailRawMessage]


class MailClient(Protocol):
    """IMAP 客户端(108):实现侧 imaplib 标准库(阻塞 IO 经线程隔离)。
    语义:`UID SEARCH UID <last_uid+1>:*` 游标续传 + UIDVALIDITY 探测;
    连接/认证/协议错误按三类抛出,由服务层分类落 FAILED run。"""

    async def fetch_since(
        self,
        *,
        host: str,
        port: int,
        tls: bool,
        user: str,
        credential: str,
        folder: str,
        last_uid: int,
        limit: int,
    ) -> MailFetch: ...


class InputGuardrail(Protocol):
    """输入护栏(108:邮件摘要是外部内容,进目标执行=进 LLM 链路,过既有
    020 护栏;实现侧组合根绑定 GuardrailService)。返回 (是否拦截,
    处理后文本,与 texts 同序——masked 脱敏产物)。"""

    async def check(
        self, *, tenant_id: str, user_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]: ...
