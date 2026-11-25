"""auth 用例层:Keycloak联邦回调、令牌生命周期、当前用户。

零框架依赖:不 import fastapi/sqlalchemy/redis;持久化经 ports 端口,配置经 Settings。
孔明不持有本地密码(ADR-0010),主体键即Keycloak `sub`。
"""

import base64
import hashlib
import secrets
from typing import cast

import jwt as pyjwt

from kongming.auth import tokens
from kongming.auth.models import (
    AccountDisabled,
    MeInfo,
    NormalizedProfile,
    ProviderExchangeFailed,
    ProviderNotFound,
    RbacUser,
    StateInvalid,
    TenantRef,
    TokenInvalid,
    TokenPair,
    User,
)
from kongming.auth.ports import (
    OAuthProvider,
    OAuthStateStore,
    OIDCProvider,
    RefreshFamilyStore,
    UnitOfWork,
)
from kongming.config import Settings
from kongming.db import uuid7

DEFAULT_ROLE_KEY = "DEVELOPER"  # 联邦自动建号的默认角色(rbac spec)
DEFAULT_TENANT_KEY = "default"


class AuthService:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        families: RefreshFamilyStore,
        states: OAuthStateStore,
        settings: Settings,
        registry: dict[str, OAuthProvider],
    ) -> None:
        self._uow = uow
        self._families = families
        self._states = states
        self._s = settings
        self._registry = registry

    # ---- 令牌签发(联邦回调与 refresh 共用) ----

    async def _issue_pair(self, user: User) -> TokenPair:
        fid = uuid7().hex
        jti = uuid7().hex
        access = tokens.issue(user.id, "access")
        refresh = tokens.issue(user.id, "refresh", extra={"fid": fid, "jti": jti})
        await self._families.issue(user.id, fid, jti, self._s.refresh_token_ttl_sec)
        return TokenPair(access, refresh)

    # ---- refresh 轮换 / 登出 ----

    def _decode_refresh(self, token: str) -> tuple[str, str, str]:
        try:
            claims = tokens.verify(token, expected_type="refresh")
        except pyjwt.PyJWTError as e:
            raise TokenInvalid("refresh token 无效或已失效") from e
        fid, jti = claims.get("fid"), claims.get("jti")
        if not isinstance(fid, str) or not isinstance(jti, str):
            raise TokenInvalid("refresh token 无效或已失效")
        return str(claims["sub"]), fid, jti

    async def refresh(self, refresh_token: str) -> TokenPair:
        user_id, fid, jti = self._decode_refresh(refresh_token)
        if await self._families.is_revoked_family(fid):
            raise TokenInvalid("refresh token 无效或已失效")
        user = await self._uow.users.get_by_id(user_id)
        if user is None or not user.active:
            raise TokenInvalid("refresh token 无效或已失效")

        rotated, tomb = await self._families.consume(jti, self._s.refresh_token_ttl_sec)
        if rotated is None:
            if tomb is not None:
                # 复用检测:旧票重放 = 泄漏信号,吊销全家族(ADR-003)
                await self._families.revoke_family(tomb, self._s.refresh_token_ttl_sec)
            raise TokenInvalid("refresh token 无效或已失效")

        new_jti = uuid7().hex
        access = tokens.issue(user.id, "access")
        refresh = tokens.issue(user.id, "refresh", extra={"fid": fid, "jti": new_jti})
        await self._families.issue(user.id, fid, new_jti, self._s.refresh_token_ttl_sec)
        return TokenPair(access, refresh)

    async def logout_sso(self, refresh_token: str) -> str | None:
        """撤销本地 refresh 家族并返回Keycloak end_session 地址;无可用端点时 None。"""
        try:
            _, fid, _ = self._decode_refresh(refresh_token)
            await self._families.revoke_family(fid, self._s.refresh_token_ttl_sec)
        except TokenInvalid:
            pass
        provider = self._registry.get("keycloak")
        if provider is None:
            return None
        end_session = getattr(provider, "end_session_url", None)
        if end_session is None:
            return None
        try:
            url: str | None = await end_session(self._s)
        except ProviderExchangeFailed:
            # 本地会话已撤销;发现文档故障不得把登出变成 500,前端回落本地登录页。
            return None
        return url

    async def logout(self, refresh_token: str) -> None:
        """幂等:无效令牌同样返回成功(不作存在性预言机)。"""
        try:
            _, fid, _ = self._decode_refresh(refresh_token)
        except TokenInvalid:
            return
        await self._families.revoke_family(fid, self._s.refresh_token_ttl_sec)

    # ---- 当前用户 ----

    async def me(self, user_id: str) -> MeInfo:
        user = await self._uow.users.get_by_id(user_id)
        if user is None or not user.active:
            raise TokenInvalid("账号不可用")
        role_keys = await self._uow.roles.list_role_keys(user.id)
        perm_codes = await self._uow.perms.list_codes(user.id)
        tenants = await self._uow.tenants.list_for_user(user.id)
        return MeInfo(
            user_id=user.id,
            username=user.username,
            nickname=user.nickname,
            role_keys=role_keys,
            perm_codes=perm_codes,
            tenants=[TenantRef(t.tenant_key, t.tenant_name) for t in tenants],
        )

    async def get_rbac_user(self, user_id: str) -> RbacUser:
        """功能权限检查专用查询:账号有效性 + 启用角色授予的有效权限,只读两处。

        与 me() 共用账号检查与 perms.list_codes()(停用角色不授予);角色名、
        租户列表等资料面数据不在此读取——权限检查用不到它们。
        """
        user = await self._uow.users.get_by_id(user_id)
        if user is None or not user.active:
            raise TokenInvalid("账号不可用")
        return RbacUser(
            user_id=user.id, perm_codes=frozenset(await self._uow.perms.list_codes(user.id))
        )

    # ---- 权限拒绝审计 ----

    async def log_perm_denied(self, user_id: str, perm_code: str, path: str) -> None:
        """权限点校验 403 的操作审计(rbac spec「撤销权限立即生效」)。

        前提锚定:本方法只在 require_perm 依赖内调用——它先于端点执行,
         session 仅有只读查询,commit 不会意外提交业务写。若未来在
        权限校验前引入写型依赖(配额预扣等),须重新评估此处 commit 语义。
        """
        await self._uow.operlog.add(user_id, "perm_denied", perm_code, {"path": path})
        await self._uow.commit()

    # ---- 联邦登录(连接器形态) ----

    def _provider(self, key: str) -> OAuthProvider:
        p = self._registry.get(key)
        if p is None or not p.configured(self._s):
            raise ProviderNotFound("provider 不存在或未配置")
        return p

    def has_provider(self, key: str) -> bool:
        p = self._registry.get(key)
        return p is not None and p.configured(self._s)

    def oauth_options(self) -> list[tuple[str, str]]:
        return [(p.key, p.display_name) for p in self._registry.values() if p.configured(self._s)]

    async def oauth_start(self, provider_key: str) -> str:
        p = self._provider(provider_key)
        state = secrets.token_urlsafe(32)
        code_verifier: str | None = None
        code_challenge: str | None = None
        if p.pkce:
            code_verifier = secrets.token_urlsafe(64)
            digest = hashlib.sha256(code_verifier.encode()).digest()
            code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        nonce = secrets.token_urlsafe(16) if p.oidc else None
        await self._states.put(
            state,
            provider=provider_key,
            code_verifier=code_verifier,
            ttl_sec=self._s.auth_state_ttl_sec,
            nonce=nonce,
        )
        if p.oidc:
            op = cast("OIDCProvider", p)
            await op.prepare(self._s)
            return op.authorize_url(
                self._s, state=state, code_challenge=code_challenge, nonce=nonce
            )
        return p.authorize_url(self._s, state=state, code_challenge=code_challenge)

    async def oauth_callback(self, provider_key: str, code: str, state: str) -> TokenPair:
        p = self._provider(provider_key)
        payload = await self._states.take(state)
        if payload is None or payload.get("provider") != provider_key:
            raise StateInvalid("state 无效或已过期")
        if p.oidc:
            profile = await cast("OIDCProvider", p).fetch_profile(
                self._s,
                code=code,
                code_verifier=payload.get("code_verifier"),
                nonce=payload.get("nonce") or None,
            )
        else:
            profile = await p.fetch_profile(
                self._s, code=code, code_verifier=payload.get("code_verifier")
            )
        user = await self._account_link(p, profile)
        if not user.active:
            raise AccountDisabled("账号已禁用")
        await self._uow.operlog.add(user.id, "auth.login", user.username, {"provider": p.key})
        await self._uow.commit()
        return await self._issue_pair(user)

    async def _account_link(self, provider: OAuthProvider, profile: NormalizedProfile) -> User:
        """按序关联(ADR-0010):identity 命中 → 以 `sub` 建档。

        只按 (provider, sub) 绑定,不做 email 关联:跨 IdP 按 email 自动合并会把
        两个真实身份错绑到同一个人。
        """
        db_provider = provider.key.upper()
        uid = await self._uow.identities.find_user_id(db_provider, profile.external_id)
        if uid is not None:
            user = await self._uow.users.get_by_id(uid)
            if user is None:  # 身份绑定悬空(用户已被硬删)——防御分支
                raise StateInvalid("身份绑定失效")
            return user
        return await self._auto_provision(db_provider, profile)

    async def _auto_provision(self, db_provider: str, profile: NormalizedProfile) -> User:
        username = await self._dedupe_username(profile.username, db_provider.lower())
        role = await self._uow.roles.get_by_key(DEFAULT_ROLE_KEY)
        tenant = await self._uow.tenants.get_by_key(DEFAULT_TENANT_KEY)
        if role is None or tenant is None:
            # 启动种子必先于流量就位(main lifespan);缺失即环境异常,拒绝建号
            raise RuntimeError("默认角色/租户缺失:种子未执行")
        # sys_user.id 即Keycloak sub:所有授权表都以该列外键关联,不再生成本地 ID
        user = User(
            id=profile.external_id,
            username=username,
            email=profile.email if profile.email_verified else None,
            nickname=profile.display_name[:64],
            status="ACTIVE",
        )
        await self._uow.users.create(user)
        await self._uow.roles.bind_user(user.id, role.id)
        await self._uow.tenants.bind_user(user.id, tenant.id)
        await self._uow.identities.create(user.id, db_provider, profile.external_id)
        await self._uow.operlog.add(
            user.id,
            "auth.register",
            username,
            {"provider": db_provider.lower(), "via": "federation"},
        )
        return user

    async def _dedupe_username(self, base: str, provider_key: str) -> str:
        """联邦建号用户名:一律 {base}-{provider} 起步(来源可溯);
        后缀名仍冲突再追加 -N(bounded)。base 超长时先给后缀留位再截断,
        保证候选彼此恒不同且 ≤64(防截断后多候选同串误报失败)。"""
        short = base[: 64 - len(provider_key) - 3]  # 预留 -provider 与 -N 的位置
        candidates = [f"{short}-{provider_key}"] + [
            f"{short}-{provider_key}-{i}" for i in range(2, 7)
        ]
        for c in candidates:
            if await self._uow.users.get_by_username(c) is None:
                return c
        raise RuntimeError("用户名脱冲突失败")
