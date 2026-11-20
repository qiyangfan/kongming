"""认证与联邦登录路由(ADR-003、ADR-006)。
后端负责校验凭证、交换 OAuth 令牌并签发 JWT;路由只返回 JSON。"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response

from kongming.auth.deps import Service, current_user
from kongming.auth.models import (
    AccountDisabled,
    AccountLocked,
    AuthError,
    CurrentPasswordWrong,
    LoginFailed,
    MeInfo,
    NoLocalPassword,
    ProviderNotFound,
    StateInvalid,
    TokenInvalid,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


# ---- 传输模型 ----


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CallbackRequest(BaseModel):
    code: str
    state: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 —— 固定字面量,非密钥
    must_change_password: bool = False


class TenantRefOut(BaseModel):
    tenant_key: str
    tenant_name: str


class MeOut(BaseModel):
    user_id: str
    username: str
    nickname: str
    role_keys: list[str]
    perm_codes: list[str]
    tenants: list[TenantRefOut]
    must_change_password: bool


class ProviderOption(BaseModel):
    provider: str
    display_name: str


class OptionsOut(BaseModel):
    providers: list[ProviderOption]


class StartOut(BaseModel):
    authorize_url: str


def _pair_out(pair: TokenPair) -> TokenPairOut:
    return TokenPairOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        must_change_password=pair.must_change_password,
    )


def _me_out(info: MeInfo) -> MeOut:
    return MeOut(
        user_id=info.user_id,
        username=info.username,
        nickname=info.nickname,
        role_keys=info.role_keys,
        perm_codes=info.perm_codes,
        tenants=[
            TenantRefOut(tenant_key=t.tenant_key, tenant_name=t.tenant_name) for t in info.tenants
        ],
        must_change_password=info.must_change_password,
    )


# ---- 端点 ----


@router.post("/login", response_model=TokenPairOut)
async def login(req: LoginRequest, svc: Service) -> TokenPairOut:
    return _pair_out(await svc.login(req.username, req.password))


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(req: RefreshRequest, svc: Service) -> TokenPairOut:
    return _pair_out(await svc.refresh(req.refresh_token))


@router.post("/logout", status_code=204)
async def logout(req: LogoutRequest, svc: Service) -> Response:
    await svc.logout(req.refresh_token)
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
async def me(user: Annotated[dict[str, object], Depends(current_user)], svc: Service) -> MeOut:
    return _me_out(await svc.me(str(user["sub"])))


@router.post("/password/change", status_code=204)
async def change_password(
    req: ChangePasswordRequest,
    user: Annotated[dict[str, object], Depends(current_user)],
    svc: Service,
) -> Response:
    await svc.change_password(str(user["sub"]), req.current_password, req.new_password)
    return Response(status_code=204)


@router.get("/options", response_model=OptionsOut)
async def options(svc: Service) -> OptionsOut:
    return OptionsOut(
        providers=[ProviderOption(provider=k, display_name=n) for k, n in svc.oauth_options()]
    )


@router.get("/{provider}/start", response_model=StartOut)
async def oauth_start(provider: str, svc: Service) -> StartOut:
    return StartOut(authorize_url=await svc.oauth_start(provider))


@router.post("/{provider}/callback", response_model=TokenPairOut)
async def oauth_callback(provider: str, req: CallbackRequest, svc: Service) -> TokenPairOut:
    return _pair_out(await svc.oauth_callback(provider, req.code, req.state))


# ---- 领域错误 → 状态码统一映射(router 唯一的规则) ----


def register_auth_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginFailed)
    async def _(_: Request, e: LoginFailed) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": "用户名或密码错误"})

    @app.exception_handler(AccountDisabled)
    async def _(_: Request, e: AccountDisabled) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "账号已禁用"})

    @app.exception_handler(AccountLocked)
    async def _(_: Request, e: AccountLocked) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": str(e)},
            headers={"Retry-After": str(e.retry_after_sec)},
        )

    @app.exception_handler(TokenInvalid)
    async def _(_: Request, e: TokenInvalid) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(e) or "令牌无效或已失效"})

    @app.exception_handler(StateInvalid)
    async def _(_: Request, e: StateInvalid) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(e) or "state 无效或已过期"})

    @app.exception_handler(ProviderNotFound)
    async def _(_: Request, e: ProviderNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(e)})

    @app.exception_handler(CurrentPasswordWrong)
    async def _(_: Request, e: CurrentPasswordWrong) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    @app.exception_handler(NoLocalPassword)
    async def _(_: Request, e: NoLocalPassword) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    @app.exception_handler(AuthError)
    async def _(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(e)})
