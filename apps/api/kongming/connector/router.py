"""连接器管理端点：/system/connectors/*。

挂 require_perm("connector:manage"),租户视角(X-Tenant-Id 白名单,与 mcp
同构);跨租户/不存在 404 同形;领域错误映射 400/404/409。凭证任何接口不回
显明文——创建/更新/列表/详情响应只含形态与尾号掩码(spec)。测试调用按
当前表单声明发起一次真实出站(显式确认在 UI 层),落既有工具审计表。
"""

import logging
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.connector.models import (
    ConnectorDecl,
    ConnectorExists,
    ConnectorNotFound,
    ConnectorValidationError,
    ParamDecl,
)
from kongming.connector.ports import ConnectorCaller
from kongming.connector.repository import SqlConnectorRepo
from kongming.connector.service import ConnectorService, masked_out_row
from kongming.datasource.cipher import get_cipher
from kongming.db import get_session
from kongming.toolcall.models import ToolAuditEntry
from kongming.toolcall.repository import ToolAuditWriter

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/connectors", tags=["connector"])

MANAGE = "connector:manage"


class _OperLogger:
    """操作审计端口绑定(与业务共用同一 session 事务)。"""

    def __init__(self, session: AsyncSession, actor_id: str) -> None:
        self._repo = SqlOperLogRepo(session)
        self._actor = actor_id

    async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
        await self._repo.add(self._actor, action, target, detail)


class ConnectorGuardrailAdapter:
    """输入护栏端口绑定(独立短会话,与 bot 上架护栏同闸)。"""

    async def check(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]:
        from kongming.db import get_sessionmaker
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            verdict = await svc.evaluate_input(
                trace_id=trace_id, user_id=user_id, tenant_id=tenant_id, texts=texts
            )
        return verdict.blocked, verdict.processed


class ConnectorTestCallAuditor:
    """工具审计端口绑定(测试调用落既有审计表;ToolAuditWriter 自带独立短事务)。"""

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
    ) -> None:
        await ToolAuditWriter().record(
            ToolAuditEntry(
                trace_id=f"connector-test-{uuid.uuid4().hex[:12]}",
                user_id=user_id,
                tenant_id=tenant_id,
                tool_name=tool_name,
                status=status,
                args_digest=args_digest,
                error_reason=error_reason,
                duration_ms=duration_ms,
                channel="test_call",
                domain="http",
            )
        )


def _build_caller() -> ConnectorCaller:
    """出站调用适配(测试调用):按声明三件套走网关切面(出口校验→参数校验
    →SSRF→出站→分类),不出网关的治理语义零复制。租户随 row 传(调用用例
    组装行时已带)。"""

    class _Caller:
        async def call(
            self, *, row: dict[str, Any], secret: str | None, args: dict[str, object]
        ) -> tuple[bool, int | None, str, str | None]:
            from kongming.toolgateway.connector import build_connector_stack
            from kongming.toolgateway.gateway import ToolGateway

            settings = get_settings()
            spec, policy, executor = build_connector_stack(
                row, auth_secret=secret, quota_per_minute=settings.toolgateway_http_quota
            )
            gw = ToolGateway(
                manifest={"http": spec},
                policies={"http": policy},
                domain_executors={"http": executor},
            )
            out = await gw.execute(
                tool_name=f"http__{row['key']}",
                args=args,
                domain="http",
                trace_id=f"test-{uuid.uuid4().hex[:12]}",
                tenant_id=str(row.get("tenant_id") or "-"),
                user_id="-",
            )
            if out["status"] == "executed":
                result = cast("dict[str, object]", out.get("result") or {})
                body = str(result.get("body") or "")
                status = result.get("status_code")
                return (
                    True,
                    status if isinstance(status, int) else None,
                    body[:500],
                    None,
                )
            reason = str(out.get("reason") or "policy: 未知拒绝")
            return False, None, "", reason

    return _Caller()


def _resolve_tenant(
    user: dict[str, object], session: AsyncSession, header: str | None
) -> tuple[Any, str | None]:
    """X-Tenant-Id 白名单解析(与 mcp/chat 同构)。"""
    repo = ChatRepository(session)
    uid = str(user["sub"])
    if header is not None:
        return repo.find_bound_tenant(user_id=uid, tenant_key=header), header
    return repo.find_default_tenant(user_id=uid), None


async def _tenant_or_403(user: dict[str, object], session: AsyncSession, header: str | None) -> str:
    pending, explicit = _resolve_tenant(user, session, header)
    found = await pending
    if found is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    if explicit is not None:
        return str(found)
    return str(found[0])


def get_connector_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[dict[str, object], Depends(current_user)],
) -> ConnectorService:
    settings = get_settings()
    return ConnectorService(
        SqlConnectorRepo(session, get_cipher(settings.datasource_encrypt_key)),
        get_cipher(settings.datasource_encrypt_key),
        ConnectorGuardrailAdapter(),
        timeout_max_ms=settings.connector_timeout_max_ms,
        caller=_build_caller(),
        auditor=ConnectorTestCallAuditor(),
        oper=_OperLogger(session, str(user["sub"])),
    )


# ── 传输层模型 ──


class ParamDeclIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=16)
    required: bool = False
    description: str = Field(default="", max_length=200)


class ConnectorIn(BaseModel):
    key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    method: str = Field(min_length=1, max_length=8)
    path_template: str = Field(min_length=1, max_length=1024)
    params: list[ParamDeclIn] = []
    allowed_hosts: list[str] = Field(max_length=10)
    auth_type: str = Field(min_length=1, max_length=16)
    # 凭证仅写入形态:创建=必填性由鉴权形态校验;更新 None/空串=不修改,
    # 填入=替换(web-admin「凭证空框语义」);任何响应不回显
    auth_secret: str | None = Field(default=None, max_length=2048)
    requires_confirmation: bool | None = None  # None=按方法缺省
    timeout_ms: int = Field(ge=1)


class TestCallIn(ConnectorIn):
    """测试调用入参:当前表单声明 + 可选示例入参;use_saved_secret=True 且
    材料留空时用存量凭证(编辑态先测后存的空框语义)。"""

    args: dict[str, object] = {}
    use_saved_secret: bool = False


class TestCallOut(BaseModel):
    ok: bool
    status_code: int | None = None
    body_summary: str = ""
    failure: str | None = None
    duration_ms: int


class ConnectorOut(BaseModel):
    """连接器条目(掩码面;凭证只出形态与尾号掩码,spec「凭证不回显」)。"""

    id: str
    tenant_id: str
    key: str
    name: str
    description: str
    method: str
    path_template: str
    params: list[dict[str, Any]]
    allowed_hosts: list[str]
    auth_type: str
    has_secret: bool
    secret_mask: str
    requires_confirmation: bool
    timeout_ms: int
    enabled: str
    created_at: Any = None
    updated_at: Any = None


def _decl(body: ConnectorIn) -> ConnectorDecl:
    return ConnectorDecl(
        key=body.key,
        name=body.name,
        description=body.description,
        method=body.method,
        path_template=body.path_template,
        params=tuple(
            ParamDecl(
                name=p.name,
                type=p.type,
                required=p.required,
                description=p.description,
            )
            for p in body.params
        ),
        allowed_hosts=tuple(body.allowed_hosts),
        auth_type=body.auth_type,
        requires_confirmation=body.requires_confirmation,
        timeout_ms=body.timeout_ms,
    )


def _secret_of(body: ConnectorIn) -> str | None:
    """凭证空框语义(None/空白=不修改;创建态空白由校验按形态判定)。"""
    secret = (body.auth_secret or "").strip()
    return secret or None


def _row_out(row: dict[str, Any]) -> ConnectorOut:
    m = masked_out_row(row)
    return ConnectorOut(**m)


# ── 管理端点 ──


@router.get("", response_model=list[ConnectorOut], dependencies=[Depends(require_perm(MANAGE))])
async def list_connectors(
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[ConnectorOut]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    return [_row_out(r) for r in await svc.list_connectors(tenant_id=tenant_id)]


@router.post(
    "",
    response_model=ConnectorOut,
    status_code=201,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def create_connector(
    body: ConnectorIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ConnectorOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        row = await svc.create(
            tenant_id=tenant_id,
            user_id=str(user["sub"]),
            decl=_decl(body),
            secret=_secret_of(body),
        )
    except ConnectorExists:
        raise HTTPException(status_code=409, detail="CONNECTOR_KEY_EXISTS") from None
    except ConnectorValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _row_out(row)


@router.get(
    "/{connector_id}", response_model=ConnectorOut, dependencies=[Depends(require_perm(MANAGE))]
)
async def get_connector(
    connector_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ConnectorOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        row = await svc.get(connector_id=connector_id, tenant_id=tenant_id)
    except ConnectorNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None  # 跨租户同形
    return _row_out(row)


@router.put(
    "/{connector_id}", response_model=ConnectorOut, dependencies=[Depends(require_perm(MANAGE))]
)
async def update_connector(
    connector_id: str,
    body: ConnectorIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> ConnectorOut:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        row = await svc.update(
            connector_id=connector_id,
            tenant_id=tenant_id,
            user_id=str(user["sub"]),
            decl=_decl(body),
            secret=_secret_of(body),
        )
    except ConnectorNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    except ConnectorValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _row_out(row)


@router.delete("/{connector_id}", status_code=204, dependencies=[Depends(require_perm(MANAGE))])
async def delete_connector(
    connector_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.delete(connector_id=connector_id, tenant_id=tenant_id, user_id=str(user["sub"]))
    except ConnectorNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


@router.post(
    "/{connector_id}/enable", status_code=204, dependencies=[Depends(require_perm(MANAGE))]
)
async def enable_connector(
    connector_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.set_enabled(
            connector_id=connector_id,
            tenant_id=tenant_id,
            user_id=str(user["sub"]),
            enabled=True,
        )
    except ConnectorNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


@router.post(
    "/{connector_id}/disable", status_code=204, dependencies=[Depends(require_perm(MANAGE))]
)
async def disable_connector(
    connector_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> None:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        await svc.set_enabled(
            connector_id=connector_id,
            tenant_id=tenant_id,
            user_id=str(user["sub"]),
            enabled=False,
        )
    except ConnectorNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None


@router.post("/test-call", response_model=TestCallOut, dependencies=[Depends(require_perm(MANAGE))])
async def test_call_connector(
    body: TestCallIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Annotated[ConnectorService, Depends(get_connector_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> TestCallOut:
    """测试调用(按当前表单声明真实出站一次;显式确认在 UI 层;落工具审计)。"""
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    try:
        out = await svc.test_call(
            tenant_id=tenant_id,
            user_id=str(user["sub"]),
            decl=_decl(body),
            secret=_secret_of(body),
            args=body.args,
            use_saved_secret_for=body.key if body.use_saved_secret else None,
        )
    except ConnectorValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return TestCallOut(**out)
