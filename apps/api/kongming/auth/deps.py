"""路由认证与权限点校验依赖。

从 auth.router 拆出:全部业务域 router 都要挂认证守卫,把它留在 router 里会让
「router 不 import 他域 router」的分层契约无法成立。本模块不 import 任何 repository。
"""

import logging
from typing import Annotated

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request

from kongming.auth import tokens
from kongming.auth.dependencies import Service
from kongming.auth.models import AuthenticatedUser, RbacUser

logger = logging.getLogger(__name__)

__all__ = ["CurrentUser", "Service", "current_user", "require_perm"]


async def current_user(request: Request) -> AuthenticatedUser:
    """Bearer access token → 具名身份依赖。缺失/无效一律 401(不泄露差异)。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        claims = tokens.verify(auth.removeprefix("Bearer "))
    except pyjwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="unauthorized") from e
    return AuthenticatedUser(user_id=str(claims["sub"]))


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]


def require_perm(perm_code: str):  # type: ignore[no-untyped-def]
    """权限点校验依赖(rbac spec「权限点校验机制」):实时查库,绑定变更立即生效。

    用法:`dependencies=[Depends(require_perm("chat:execute"))]`。
    认证失败 401(先于本依赖,current_user 抛);有认证无权限 403 并落操作审计
    (rbac spec「撤销权限立即生效」:审计记录该 403;审计写失败不阻断拒绝)。
    """

    async def _dep(
        request: Request,
        user: CurrentUser,
        svc: Service,
    ) -> None:
        rbac: RbacUser = await svc.get_rbac_user(user.user_id)
        if perm_code not in rbac.perm_codes:
            try:
                await svc.log_perm_denied(user.user_id, perm_code, request.url.path)
            except Exception:  # 审计尽力而为,拒绝本身不受影响
                logger.warning("perm_denied 审计写入失败", exc_info=True)
            raise HTTPException(status_code=403, detail="forbidden")

    return _dep
