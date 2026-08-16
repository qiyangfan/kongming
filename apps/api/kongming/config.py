"""控制面配置。prod fail-closed:敏感项无默认值,缺失即拒启。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KM_", extra="ignore")

    app_name: str = "kongming-api"
    environment: str = "dev"  # dev | prod

    database_url: str = "postgresql+asyncpg://kongming:kongming@localhost:5432/kongming"
    redis_url: str = "redis://localhost:6379/0"

    # JWT(ADR-003:RS256,私钥签发、公钥验签;prod 必须显式提供)
    jwt_private_key_pem: str | None = None
    jwt_public_key_pem: str | None = None
    jwt_issuer: str = "kongming"
    access_token_ttl_sec: int = 900        # 15min
    refresh_token_ttl_sec: int = 7 * 24 * 3600

    # 内部服务令牌:api ↔ agent 通道(与用户 JWT 分离,ADR-002)
    internal_token: str | None = None

    # GitHub OAuth 联邦
    github_client_id: str | None = None
    github_client_secret: str | None = None
    # 须与 GitHub OAuth App 的 Authorization callback URL 精确一致(vite 默认端口 5173)
    github_redirect_uri: str = "http://localhost:5173/auth/github/callback"

    # 动态数据源密码加密密钥
    datasource_encrypt_key: str | None = None

    # Agent 面回调地址
    agent_base_url: str = "http://localhost:8001"

    def fail_closed_checks(self) -> None:
        """prod 环境缺敏感配置直接拒启(dev 宽松以便本地起步)。"""
        if self.environment != "prod":
            return
        required = (
            ("KM_JWT_PRIVATE_KEY_PEM", self.jwt_private_key_pem),
            ("KM_JWT_PUBLIC_KEY_PEM", self.jwt_public_key_pem),
            ("KM_INTERNAL_TOKEN", self.internal_token),
            ("KM_DATASOURCE_ENCRYPT_KEY", self.datasource_encrypt_key),
        )
        missing = [env for env, val in required if not val]
        if missing:
            raise RuntimeError(f"prod fail-closed: 缺少必需配置 {missing}")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.fail_closed_checks()
    return s
