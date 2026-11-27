"""管理台认证路由(/admin-auth)。

独立 OIDC client、state cookie、refresh cookie(`km_admin_refresh`)与
30m 会话;浏览器经管理台同源 `/api` 反代调用。
"""

import logging
import secrets
from typing import Annotated
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Cookie, Depends, FastAPI, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from kongming.adminauth.provider import AdminKeycloakProvider
from kongming.auth.deps import CurrentUser
from kongming.auth.login_limit import LoginRateLimiter
from kongming.auth.models import MeInfo, RequiredRoleMissing, TokenPair
from kongming.auth.ports import OAuthProvider
from kongming.auth.repository import SqlUnitOfWork
from kongming.auth.service import AuthService
from kongming.auth.stores import RedisOAuthStateStore, RedisRefreshFamilyStore
from kongming.config import get_settings
from kongming.db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin-auth", tags=["admin-auth"])

STATE_COOKIE = "km_admin_oidc_state"
_STATE_COOKIE_MAX = 8
_login_limiter = LoginRateLimiter()

_registry: dict[str, OAuthProvider] = {"keycloak": AdminKeycloakProvider()}


def _decode_states(raw: str | None) -> list[str]:
    return [state for state in (raw or "").split(".") if state]


def _encode_states(states: list[str]) -> str:
    unique = list(dict.fromkeys(state for state in states if state))
    return ".".join(unique[-_STATE_COOKIE_MAX:])


def _set_state_cookie(response: Response, value: str) -> None:
    s = get_settings()
    response.set_cookie(
        STATE_COOKIE,
        value,
        max_age=s.auth_state_ttl_sec,
        httponly=True,
        secure=s.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def _set_refresh_cookie(response: Response, refresh: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.admin_cookie_name,
        refresh,
        max_age=s.admin_session_ttl_sec,
        httponly=True,
        # 管理台会话生产恒 Secure(不依赖部署是否记得打开 auth_cookie_secure)
        secure=s.auth_cookie_secure or s.environment == "prod",
        samesite="lax",
        path="/",
    )


async def get_admin_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthService:
    from kongming.redis_client import get_redis

    redis = get_redis()
    s = get_settings()
    return AuthService(
        SqlUnitOfWork(session),
        families=RedisRefreshFamilyStore(redis),
        states=RedisOAuthStateStore(redis),
        settings=s,
        registry=_registry,
        refresh_ttl_sec=s.admin_session_ttl_sec,
    )


Service = Annotated[AuthService, Depends(get_admin_auth_service)]


class CallbackIn(BaseModel):
    code: str
    state: str


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 —— 固定字面量,非密钥


class StartOut(BaseModel):
    authorize_url: str


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


def _pair_out(pair: TokenPair) -> TokenPairOut:
    return TokenPairOut(access_token=pair.access_token, refresh_token=pair.refresh_token)


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


@router.get("/{provider}/start", response_model=StartOut)
async def admin_start(
    provider: str, request: Request, response: Response, svc: Service
) -> StartOut | JSONResponse:
    s = get_settings()
    client = request.client.host if request.client else "unknown"
    if not _login_limiter.allow(client, s.auth_login_rate_per_minute):
        return JSONResponse(status_code=429, content={"detail": "登录发起过于频繁,请稍后再试"})
    url = await svc.oauth_start(provider)
    state = parse_qs(urlparse(url).query).get("state", [""])[0]
    if state:
        states = _decode_states(request.cookies.get(STATE_COOKIE))
        _set_state_cookie(response, _encode_states([*states, state]))
    return StartOut(authorize_url=url)


@router.post("/{provider}/callback", response_model=TokenPairOut)
async def admin_callback(
    provider: str,
    req: CallbackIn,
    response: Response,
    svc: Service,
    state_cookie: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
) -> TokenPairOut:
    if not svc.has_provider(provider):
        from kongming.auth.models import ProviderNotFound

        raise ProviderNotFound("provider 不存在或未配置")
    states = _decode_states(state_cookie)
    target = req.state.encode("utf-8", "surrogatepass")
    if not any(secrets.compare_digest(candidate.encode(), target) for candidate in states):
        from kongming.auth.models import StateInvalid

        raise StateInvalid("state 未绑定当前浏览器")
    pair = await svc.oauth_callback(provider, req.code, req.state)
    remaining = [
        candidate for candidate in states if not secrets.compare_digest(candidate.encode(), target)
    ]
    if remaining:
        _set_state_cookie(response, _encode_states(remaining))
    else:
        response.delete_cookie(STATE_COOKIE, path="/")
    _set_refresh_cookie(response, pair.refresh_token)
    return _pair_out(pair)


@router.post("/refresh", response_model=TokenPairOut)
async def admin_refresh(
    request: Request,
    response: Response,
    svc: Service,
) -> TokenPairOut:
    from kongming.auth.models import TokenInvalid

    refresh_cookie = request.cookies.get(get_settings().admin_cookie_name)
    if not refresh_cookie:
        raise TokenInvalid("refresh token 缺失")
    pair = await svc.refresh(refresh_cookie)
    _set_refresh_cookie(response, pair.refresh_token)
    return _pair_out(pair)


@router.post("/logout", status_code=204)
async def admin_logout(
    request: Request,
    response: Response,
    svc: Service,
) -> Response:
    await svc.logout(request.cookies.get(get_settings().admin_cookie_name) or "")
    response.delete_cookie(get_settings().admin_cookie_name, path="/")
    response.status_code = 204
    return response


@router.post("/logout/sso")
async def admin_logout_sso(
    request: Request, response: Response, svc: Service
) -> dict[str, str | None]:
    target = await svc.logout_sso(request.cookies.get(get_settings().admin_cookie_name) or "")
    response.delete_cookie(get_settings().admin_cookie_name, path="/")
    return {"redirect_url": target}


@router.get("/me", response_model=MeOut)
async def admin_me(user: CurrentUser, svc: Service) -> MeOut:
    return _me_out(await svc.me(user.user_id))


def register_adminauth_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequiredRoleMissing)
    async def _(_: Request, e: RequiredRoleMissing) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(e)})
