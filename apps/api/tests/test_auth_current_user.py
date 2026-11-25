"""当前用户依赖(auth「本地令牌与会话」):access 令牌 → 具名身份对象。

身份以属性访问暴露 user_id;缺失/无效/用途错误的令牌一律 401(不泄露差异)。
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from kongming.auth import tokens
from kongming.auth.deps import current_user
from kongming.auth.models import AuthenticatedUser


def _request_with_token(token: str) -> Request:
    return Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


async def test_access_token_yields_authenticated_user_with_same_user_id() -> None:
    uid = "01936d7e-8c1b-7cc2-9d4e-5fa1b2c3d4e5"
    user = await current_user(_request_with_token(tokens.issue(uid, "access")))
    # 旧实现返回 claims 字典;具名对象是本依赖的对外约定
    assert isinstance(user, AuthenticatedUser)
    assert user.user_id == uid


async def test_missing_authorization_header_rejected() -> None:
    with pytest.raises(HTTPException) as ei:
        await current_user(Request({"type": "http", "headers": []}))
    assert ei.value.status_code == 401


async def test_garbage_token_rejected() -> None:
    with pytest.raises(HTTPException) as ei:
        await current_user(_request_with_token("not-a-jwt"))
    assert ei.value.status_code == 401


async def test_refresh_token_rejected_for_access_use() -> None:
    uid = "01936d7e-8c1b-7cc2-9d4e-5fa1b2c3d4e6"
    with pytest.raises(HTTPException) as ei:
        await current_user(_request_with_token(tokens.issue(uid, "refresh")))
    assert ei.value.status_code == 401


async def test_expired_token_rejected() -> None:
    uid = "01936d7e-8c1b-7cc2-9d4e-5fa1b2c3d4e7"
    token = tokens.issue(uid, "access", extra={"exp": 1000000000})
    with pytest.raises(HTTPException) as ei:
        await current_user(_request_with_token(token))
    assert ei.value.status_code == 401
