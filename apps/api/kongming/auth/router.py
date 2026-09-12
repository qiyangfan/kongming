"""认证路由(ADR-003/006):登录页即前端路由,后端只出 JSON 与 JWT(薄适配层)。

POST /auth/login                账密 → 令牌对(锁定/禁用/首登改密)
POST /auth/refresh              refresh 轮换(旧票复用 → 吊销全家族)
POST /auth/logout               吊销 refresh 所属家族(幂等 204)
GET  /auth/me                   当前用户(真实角色/权限点/租户)
POST /auth/password/change      自助改密(吊销全部家族,全端重登)
GET  /auth/options              已配置联邦 provider 清单(前端按钮数据驱动)
GET  /auth/{provider}/start     发起联邦(一次性 state;PKCE 按家声明)
POST /auth/{provider}/callback  联邦回调(code+state → 令牌对;OAuth token 不出后端)
"""

import logging
from typing import Annotated

import jwt as pyjwt
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse, Response

from kongming.auth import providers, tokens
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
from kongming.auth.repository import SqlUnitOfWork
from kongming.auth.service import AuthService
from kongming.auth.stores import (
    RedisLoginAttemptStore,
    RedisOAuthStateStore,
    RedisRefreshFamilyStore,
)
from kongming.config import get_settings
from kongming.db import get_session
from kongming.redis_client import get_redis

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


# ---- 依赖装配(唯一知道"哪个端口绑哪个实现"的地方) ----


async def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    return AuthService(
        SqlUnitOfWork(session),
        attempts=RedisLoginAttemptStore(redis),
        families=RedisRefreshFamilyStore(redis),
        states=RedisOAuthStateStore(redis),
        settings=get_settings(),
        registry=providers.REGISTRY,
    )


Service = Annotated[AuthService, Depends(get_auth_service)]


async def current_user(request: Request) -> dict[str, object]:
    """Bearer access token → claims 依赖。缺失/无效一律 401(不泄露差异)。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        claims = tokens.verify(auth.removeprefix("Bearer "))
    except pyjwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="unauthorized") from e
    return claims


def require_perm(perm_code: str):  # type: ignore[no-untyped-def]
    """权限点校验依赖(rbac spec「权限点校验机制」):实时查库,绑定变更立即生效。

    用法:`dependencies=[Depends(require_perm("chat:execute"))]`。
    认证失败 401(先于本依赖,current_user 抛);有认证无权限 403 并落操作审计
    (rbac spec「撤销权限立即生效」:审计记录该 403;审计写失败不阻断拒绝)。
    """

    async def _dep(
        request: Request,
        user: Annotated[dict[str, object], Depends(current_user)],
        svc: Service,
    ) -> None:
        info = await svc.me(str(user["sub"]))
        if perm_code not in info.perm_codes:
            try:
                await svc.log_perm_denied(str(user["sub"]), perm_code, request.url.path)
            except Exception:  # noqa: BLE001  # 审计尽力而为,拒绝本身不受影响
                logger.warning("perm_denied 审计写入失败", exc_info=True)
            raise HTTPException(status_code=403, detail="forbidden")

    return _dep


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
