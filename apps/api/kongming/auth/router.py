"""认证路由(ADR-003):登录页即前端路由,后端只出 JSON 与 JWT。

POST /auth/login           账密 → {access, refresh}(user store 待接入)
POST /auth/refresh         refresh 轮换(body 传参,不经 URL 防日志泄漏)→ 新对
GET  /auth/me              当前用户(Bearer 解析)
GET  /auth/github/start    发起 GitHub OAuth
GET  /auth/github/callback GitHub 回调 → JWT(建号逻辑待接入)
"""

from typing import Annotated

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kongming.auth import password as pwd
from kongming.auth import tokens

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 —— 固定字面量,非密钥(命名触发启发式误报)


@router.post("/login", response_model=TokenPair)
async def login(req: LoginRequest) -> TokenPair:
    # TODO(user-store): 查 sys_user + verify(pwd) + 失败计数锁定;骨架期返回 501
    _ = (req.username, pwd.hash_password(req.password))
    raise HTTPException(status_code=501, detail="user store 待接入(baseline 已建表)")


@router.post("/refresh", response_model=TokenPair)
async def refresh(req: RefreshRequest) -> TokenPair:
    try:
        claims = tokens.verify(req.refresh_token, expected_type="refresh")
    except pyjwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid refresh token: {e}") from e
    subject = str(claims["sub"])
    return TokenPair(
        access_token=tokens.issue(subject, "access"),
        refresh_token=tokens.issue(subject, "refresh"),
    )


@router.get("/github/start")
async def github_start() -> dict[str, str]:
    from kongming.auth.github import GITHUB_AUTH_URL, client

    c = client()
    url, _state = c.create_authorization_url(GITHUB_AUTH_URL, scope="read:user user:email")
    return {"authorize_url": url}


@router.get("/github/callback", response_model=TokenPair)
async def github_callback(code: str) -> TokenPair:
    from kongming.auth import github

    profile = await github.exchange_and_profile(code)
    # TODO(user-store): (GITHUB, str(profile["id"])) 查/建号 → 签发
    raise HTTPException(
        status_code=501, detail=f"github 用户 {profile.get('login')} 建号逻辑待接入"
    )


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


@router.get("/me")
async def me(user: Annotated[dict[str, object], Depends(current_user)]) -> dict[str, object]:
    # TODO(rbac): perm_codes 由权限域填充
    return {"user_id": user["sub"], "perm_codes": []}
