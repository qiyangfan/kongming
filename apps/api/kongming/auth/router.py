"""认证路由(ADR-003、ADR-0010)。

孔明不提供本地账密登录:登录入口只有Keycloak OIDC;后端仍在联邦回调后签发本地
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

# 并发 tab 上限:cookie 存点分隔 state 列表,超出丢最旧
_STATE_COOKIE_MAX = 8

_login_limiter = LoginRateLimiter()


def _decode_states(raw: str | None) -> list[str]:
    """state cookie → 列表(旧单值形态天然兼容;空段过滤)。

    state 由 secrets.token_urlsafe 生成,字母表不含点,点分隔不歧义。"""
    if not raw:
        return []
    return [state for state in raw.split(".") if state]


def _encode_states(states: list[str]) -> str:
    """state 列表 → cookie 值:去重保序,最多保留最近 _STATE_COOKIE_MAX 个。"""
    unique = list(dict.fromkeys(state for state in states if state))
    return ".".join(unique[-_STATE_COOKIE_MAX:])


def _set_state_cookie(response: Response, value: str) -> None:
    """写 state cookie(属性单源;start 与 callback 共用)。"""
    s = get_settings()
    response.set_cookie(
        STATE_COOKIE,
        value,
        max_age=s.auth_state_ttl_sec,
        httponly=True,
        secure=s.auth_cookie_secure,
        samesite="lax",
        # 前端统一经 /api 前缀调用(dev 由 Vite 代理重写),cookie 须同时覆盖 /auth 与 /api/auth。
        path="/",
    )


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
        # 同上:前端以 /api/auth/refresh 调用,路径必须覆盖 /api 前缀。
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/")


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
        states = _decode_states(request.cookies.get(STATE_COOKIE))
        _set_state_cookie(response, _encode_states([*states, state]))
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
    states = _decode_states(state_cookie)
    # compare_digest 的 str 形态只接受 ASCII;按字节比较,任意回调参数都不会抛编解码异常
    target = req.state.encode("utf-8", "surrogatepass")
    if not any(secrets.compare_digest(candidate.encode(), target) for candidate in states):
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
