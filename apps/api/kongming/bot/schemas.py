"""bot 请求/响应模型与行投影(模型类名冻结:OpenAPI components 单源)。"""

from datetime import datetime
from typing import cast

from pydantic import BaseModel, Field


class BotIn(BaseModel):
    bot_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=128)
    system_prompt: str = Field(min_length=1, max_length=4000)
    tools: list[str] = Field(default_factory=list)
    datasource_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{1,63}$")
    model: str | None = Field(default=None, max_length=64)
    is_public: bool = False


class BotOut(BaseModel):
    id: str
    bot_key: str
    name: str
    system_prompt: str
    tools: list[str]
    datasource_key: str
    model: str | None
    enabled: bool
    is_public: bool
    published_at: str | None = None  # 上架态(None=未上架)
    team_id: str | None = None  # 归属回传(None=个人;契约)
    created_at: str


class BotUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = Field(default=None, max_length=4000)
    tools: list[str] | None = None
    datasource_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    model: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    is_public: bool | None = None


class ApiKeyOut(BaseModel):
    id: str
    name: str
    revoked_at: str | None
    last_used_at: str | None
    created_at: str


class BotChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class BotChatOut(BaseModel):
    success: bool
    summary: str
    error: str | None = None


class OpenAiMessage(BaseModel):
    role: str
    content: str


class OpenAiChatIn(BaseModel):
    model: str | None = None
    messages: list[OpenAiMessage]


def _iso_or_none(v: object) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def to_bot_out(r: dict[str, object]) -> BotOut:
    """bot 行 → 管理列表回显投影。"""
    return BotOut(
        id=str(r["id"]),
        bot_key=str(r["bot_key"]),
        name=str(r["name"]),
        system_prompt=str(r["system_prompt"]),
        tools=(
            [str(t) for t in cast("list[object]", r["tools"])]
            if isinstance(r["tools"], list)
            else []
        ),
        datasource_key=str(r["datasource_key"]),
        model=None if r["model"] is None else str(r["model"]),
        enabled=bool(r["enabled"]),
        is_public=bool(r["is_public"]),
        published_at=_iso_or_none(r.get("published_at")),
        team_id=str(r["team_id"]) if r.get("team_id") else None,
        created_at=str(r["created_at"]),
    )
