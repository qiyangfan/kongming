"""auth 用例层:登录/令牌生命周期/当前用户/改密/联邦回调/种子(宪法 Article II 领域层)。

零框架依赖:不 import fastapi/sqlalchemy/redis;持久化经 ports 端口,配置经 Settings。
"""

import base64
import hashlib
import secrets
from datetime import UTC, datetime

import jwt as pyjwt

from kongming.auth import password as pwd
from kongming.auth import tokens
from kongming.auth.models import (
    AccountDisabled,
    AccountLocked,
    CurrentPasswordWrong,
    LoginFailed,
    MeInfo,
    NoLocalPassword,
    NormalizedProfile,
    ProviderNotFound,
    StateInvalid,
    TenantRef,
    TokenInvalid,
    TokenPair,
    User,
)
from kongming.auth.ports import (
    LoginAttemptStore,
    OAuthProvider,
    OAuthStateStore,
    RefreshFamilyStore,
    UnitOfWork,
)
from kongming.config import Settings
from kongming.db import uuid7

DEFAULT_ROLE_KEY = "DEVELOPER"  # 联邦自动建号的默认角色(rbac spec)
DEFAULT_TENANT_KEY = "default"

# 防枚举:用户名不存在时对预置 dummy 哈希执行 argon2 verify,拉平与真实校验的时耗差
_dummy_hash: str | None = None


def _ensure_dummy_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = pwd.hash_password(secrets.token_urlsafe(16))
    return _dummy_hash


class AuthService:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        attempts: LoginAttemptStore,
        families: RefreshFamilyStore,
        states: OAuthStateStore,
        settings: Settings,
        registry: dict[str, OAuthProvider],
    ) -> None:
        self._uow = uow
        self._attempts = attempts
        self._families = families
        self._states = states
        self._s = settings
        self._registry = registry

    # ---- 令牌签发(登录与联邦回调共用) ----

    async def _issue_pair(self, user: User) -> TokenPair:
        fid = uuid7().hex
        jti = uuid7().hex
        access = tokens.issue(user.id, "access")
        refresh = tokens.issue(user.id, "refresh", extra={"fid": fid, "jti": jti})
        await self._families.issue(user.id, fid, jti, self._s.refresh_token_ttl_sec)
        # 纯联邦账号(无本地密码)不适用强制改密
        must_change = user.password_hash is not None and user.password_changed_at is None
        return TokenPair(access, refresh, must_change)

    # ---- 账密登录 ----

    async def login(self, username: str, password: str) -> TokenPair:
        ttl = await self._attempts.locked_ttl(username)
        if ttl > 0:
            raise AccountLocked(ttl)

        user = await self._uow.users.get_by_username(username)
        candidate = user.password_hash if (user and user.password_hash) else _ensure_dummy_hash()
        ok = pwd.verify_password(password, candidate)
        if not ok or user is None or user.deleted:
            # 失败计数对存在与不存在账号同键同策(防枚举);禁用账号的密码错误也走此路
            lock_sec = self._s.auth_login_lock_min * 60
            n = await self._attempts.incr_fails(username, lock_sec)
            if n >= self._s.auth_login_max_fails:
                await self._attempts.lock(username, lock_sec)
                raise AccountLocked(lock_sec)
            raise LoginFailed("用户名或密码错误")
        if not user.active:
            raise AccountDisabled("账号已禁用")

        await self._attempts.clear_fails(username)
        await self._uow.users.touch_last_login(user.id)
        await self._uow.commit()
        return await self._issue_pair(user)

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
        must_change = user.password_hash is not None and user.password_changed_at is None
        return TokenPair(access, refresh, must_change)

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
            must_change_password=user.password_hash is not None
            and user.password_changed_at is None,
        )

    # ---- 权限拒绝审计 ----

    async def log_perm_denied(self, user_id: str, perm_code: str, path: str) -> None:
        """权限点校验 403 的操作审计(rbac spec「撤销权限立即生效」)。

        前提锚定:本方法只在 require_perm 依赖内调用——它先于端点执行,
        此前 session 仅有只读查询,commit 不会意外提交业务写。若未来在
        权限校验前引入写型依赖(配额预扣等),须重新评估此处 commit 语义。
        """
        await self._uow.operlog.add(
            user_id, "perm_denied", perm_code, {"path": path}
        )
        await self._uow.commit()

    # ---- 自助改密 ----

    async def change_password(self, user_id: str, current: str, new: str) -> None:
        user = await self._uow.users.get_by_id(user_id)
        if user is None or not user.active:
            raise TokenInvalid("账号不可用")
        if user.password_hash is None:
            raise NoLocalPassword("该账号无本地密码,无法自助改密")
        if not pwd.verify_password(current, user.password_hash):
            raise CurrentPasswordWrong("当前密码错误")
        await self._uow.users.update_password(user.id, pwd.hash_password(new))
        await self._uow.commit()
        # 全端重新登录:吊销该用户全部 refresh 家族
        await self._families.revoke_all_user_families(user.id, self._s.refresh_token_ttl_sec)

    # ---- 联邦登录(连接器形态) ----

    def _provider(self, key: str) -> OAuthProvider:
        p = self._registry.get(key)
        if p is None or not p.configured(self._s):
            raise ProviderNotFound("provider 不存在或未配置")
        return p

    def oauth_options(self) -> list[tuple[str, str]]:
        return [
            (p.key, p.display_name) for p in self._registry.values() if p.configured(self._s)
        ]

    async def oauth_start(self, provider_key: str) -> str:
        p = self._provider(provider_key)
        state = secrets.token_urlsafe(32)
        code_verifier: str | None = None
        code_challenge: str | None = None
        if p.pkce:
            code_verifier = secrets.token_urlsafe(64)
            digest = hashlib.sha256(code_verifier.encode()).digest()
            code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        await self._states.put(
            state,
            provider=provider_key,
            code_verifier=code_verifier,
            ttl_sec=self._s.auth_state_ttl_sec,
        )
        return p.authorize_url(self._s, state=state, code_challenge=code_challenge)

    async def oauth_callback(self, provider_key: str, code: str, state: str) -> TokenPair:
        p = self._provider(provider_key)
        payload = await self._states.take(state)
        if payload is None or payload.get("provider") != provider_key:
            raise StateInvalid("state 无效或已过期")
        profile = await p.fetch_profile(
            self._s, code=code, code_verifier=payload.get("code_verifier")
        )
        user = await self._account_link(p.key, profile)
        if not user.active:
            raise AccountDisabled("账号已禁用")
        await self._uow.operlog.add(
            user.id, "auth.login", user.username, {"provider": p.key}
        )
        await self._uow.commit()
        return await self._issue_pair(user)

    async def _account_link(self, provider_key: str, profile: NormalizedProfile) -> User:
        """按序关联(ADR-006):identity 命中 → verified email 关联 → 自动建号。"""
        db_provider = provider_key.upper()
        uid = await self._uow.identities.find_user_id(db_provider, profile.external_id)
        if uid is not None:
            user = await self._uow.users.get_by_id(uid)
            if user is None:  # 身份绑定悬空(用户已被硬删)——防御分支
                raise StateInvalid("身份绑定失效")
            return user

        if profile.email and profile.email_verified:
            existing = await self._uow.users.get_by_email(profile.email)
            if existing is not None:
                await self._uow.identities.create(existing.id, db_provider, profile.external_id)
                return existing

        return await self._auto_provision(db_provider, profile)

    async def _auto_provision(self, db_provider: str, profile: NormalizedProfile) -> User:
        username = await self._dedupe_username(profile.username, db_provider.lower())
        role = await self._uow.roles.get_by_key(DEFAULT_ROLE_KEY)
        tenant = await self._uow.tenants.get_by_key(DEFAULT_TENANT_KEY)
        if role is None or tenant is None:
            # 启动种子必先于流量就位(main lifespan);缺失即环境异常,拒绝建号
            raise RuntimeError("默认角色/租户缺失:种子未执行")
        user = User(
            id=str(uuid7()),
            username=username,
            email=profile.email if profile.email_verified else None,
            password_hash=None,
            nickname=profile.display_name[:64],
            status="ACTIVE",
            # 纯联邦账号无本地密码,强制改密不适用(视为已改)
            password_changed_at=datetime.now(UTC),
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
        """联邦建号用户名:一律 {base}-{provider} 起步(来源可溯,003 e2e 后拍板);
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
