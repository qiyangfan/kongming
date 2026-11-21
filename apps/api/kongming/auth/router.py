"""认证路由(ADR-003、ADR-0010)。

孔明不提供本地账密登录:登录入口只有虎符 OIDC;后端仍在联邦回调后签发本地
会话令牌,路由只返回 JSON。"""

import logging
import secrets
from typing import Annotated
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Cookie, Depends, FastAPI, Request, Response
from pydantic import BaseModel
from starlette.responses import JSONResponse

from kongming.auth.deps import Service, current_user
from kongming.auth.login_limit import LoginRateLimiter
from kongming.auth.models import (
    AccountDisabled,
    AuthError,
    MeInfo,
    ProviderNotFound,
    StateInvalid,
    TokenInvalid,
    TokenPair,
)
from kongming.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


# ---- 传输模型 ----


REFRESH_COOKIE = "km_refresh"
STATE_COOKIE = "km_oidc_state"

_login_limiter = LoginRateLimiter()


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class CallbackRequest(BaseModel):
    code: str
    state: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 —— 固定字面量,非密钥


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
    )


# ---- 端点 ----


def _set_refresh_cookie(response: Response, refresh: str) -> None:
    s = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=s.refresh_token_ttl_sec,
        httponly=True,
        secure=s.auth_cookie_secure,
        samesite="lax",
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/auth")


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(
    req: RefreshRequest,
    response: Response,
    svc: Service,
    refresh_cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenPairOut:
    presented = req.refresh_token or refresh_cookie
    if not presented:
        raise TokenInvalid("refresh token 缺失")
    pair = await svc.refresh(presented)
    _set_refresh_cookie(response, pair.refresh_token)
    return _pair_out(pair)


@router.post("/logout", status_code=204)
async def logout(
    req: LogoutRequest,
    response: Response,
    svc: Service,
    refresh_cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> Response:
    await svc.logout(req.refresh_token or refresh_cookie or "")
    _clear_refresh_cookie(response)
    response.status_code = 204
    return response


@router.post("/logout/sso")
async def logout_sso(
    req: LogoutRequest,
    response: Response,
    svc: Service,
    refresh_cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> dict[str, str | None]:
    target = await svc.logout_sso(req.refresh_token or refresh_cookie or "")
    _clear_refresh_cookie(response)
    return {"redirect_url": target}


@router.get("/me", response_model=MeOut)
async def me(user: Annotated[dict[str, object], Depends(current_user)], svc: Service) -> MeOut:
    return _me_out(await svc.me(str(user["sub"])))


@router.get("/options", response_model=OptionsOut)
async def options(svc: Service) -> OptionsOut:
    return OptionsOut(
        providers=[ProviderOption(provider=k, display_name=n) for k, n in svc.oauth_options()]
    )


@router.get("/{provider}/start", response_model=StartOut)
async def oauth_start(
    provider: str, request: Request, response: Response, svc: Service
) -> StartOut | JSONResponse:
    s = get_settings()
    client = request.client.host if request.client else "unknown"
    if not _login_limiter.allow(client, s.auth_login_rate_per_minute):
        return JSONResponse(status_code=429, content={"detail": "登录发起过于频繁,请稍后再试"})
    url = await svc.oauth_start(provider)
    state = parse_qs(urlparse(url).query).get("state", [""])[0]
    if state:
        response.set_cookie(
            STATE_COOKIE,
            state,
            max_age=s.auth_state_ttl_sec,
            httponly=True,
            secure=s.auth_cookie_secure,
            samesite="lax",
            path="/auth",
        )
    return StartOut(authorize_url=url)


@router.post("/{provider}/callback", response_model=TokenPairOut)
async def oauth_callback(
    provider: str,
    req: CallbackRequest,
    response: Response,
    svc: Service,
    state_cookie: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
) -> TokenPairOut:
    if not svc.has_provider(provider):
        raise ProviderNotFound("provider 不存在或未配置")
    if not state_cookie or not secrets.compare_digest(state_cookie, req.state):
        raise StateInvalid("state 未绑定当前浏览器")
    pair = await svc.oauth_callback(provider, req.code, req.state)
    response.delete_cookie(STATE_COOKIE, path="/auth")
    _set_refresh_cookie(response, pair.refresh_token)
    return _pair_out(pair)


# ---- 领域错误 → 状态码统一映射(router 唯一的规则) ----


def register_auth_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AccountDisabled)
    async def _(_: Request, e: AccountDisabled) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "账号已禁用"})

    @app.exception_handler(TokenInvalid)
    async def _(_: Request, e: TokenInvalid) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(e) or "令牌无效或已失效"})

    @app.exception_handler(StateInvalid)
    async def _(_: Request, e: StateInvalid) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(e) or "state 无效或已过期"})

    @app.exception_handler(ProviderNotFound)
    async def _(_: Request, e: ProviderNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(e)})

    @app.exception_handler(AuthError)
    async def _(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(e)})
