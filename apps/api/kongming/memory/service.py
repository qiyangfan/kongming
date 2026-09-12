"""memory 用例编排(022):召回/显式形成/隐式落库/自管理。

零框架纪律:不 import fastapi/sqlalchemy/redis;SQL 全在 repository。
"""

import logging
from typing import Protocol

from kongming.memory.models import contains_sensitive, extract_explicit_intent
from kongming.memory.ports import Embedder, MemoryOperLogger, MemoryRepo

log = logging.getLogger(__name__)


class MemoryNotFound(Exception):
    """记忆不存在或不属于当前用户+租户(404 同形,防探测)。"""


class SensitiveContentRejected(Exception):
    """PII/凭证形态:拒绝落库(spec「PII 拒绝落库」)。"""


class TeamRoles(Protocol):
    """032:用户团队角色映射(实现侧 team 域;未注入=仅个人召回)。"""

    async def role_map(self, user_id: str) -> dict[str, str]: ...


class MemoryService:
    def __init__(
        self,
        repo: MemoryRepo,
        embedder: Embedder,
        oper_logger: MemoryOperLogger,
        team_roles: TeamRoles | None = None,  # 032
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._oper = oper_logger
        self._team_roles = team_roles

    async def _embed_quietly(self, texts: list[str]) -> list[float] | None:
        try:
            return (await self._embedder.embed(texts))[0]
        except Exception:
            log.warning("记忆 embedding 失败(降级无向量)", exc_info=True)
            return None

    async def recall_hint(self, *, user_id: str, tenant_id: str, question: str) -> str:
        """召回注入段(memory_hint):失败/空召回 → 空串(调用方不注入,零回归)。
        032:个人 ∪ 成员团队双路(role_map 经端口注入,未注入=仅个人零回归)。"""
        try:
            vector = await self._embed_quietly([question])
            if vector is None:
                return ""
            await self._repo.mark_dormant_lazy(user_id=user_id, tenant_id=tenant_id)
            team_ids: list[str] = []
            if self._team_roles is not None:
                team_ids = list((await self._team_roles.role_map(user_id)).keys())
            rows = await self._repo.recall(
                user_id=user_id, tenant_id=tenant_id, vector=vector, team_ids=team_ids
            )
            await self._repo.commit()
        except Exception:
            log.warning("记忆召回失败(降级无注入)", exc_info=True)
            return ""
        if not rows:
            return ""
        lines = [f"- {r['content']}" for r in rows]
        return "关于这位用户的已知信息:\n" + "\n".join(lines)

    async def explicit_intent(self, question: str) -> str | None:
        """显式意图识别(chat 挂接用)。"""
        return extract_explicit_intent(question)

    async def form_explicit(
        self,
        *,
        user_id: str,
        tenant_id: str,
        content: str,
        team_id: str | None = None,  # 032:团队记忆(成员校验在 router)
    ) -> None:
        """显式形成:敏感拒落;embedding 失败降级无向量(可后续编辑补)。
        032:team_id 非空=团队记忆(隐式提取维持仅个人,team_id NULL)。"""
        if contains_sensitive(content):
            await self._oper.add(
                user_id=user_id,
                action="memory.reject_sensitive",
                target=None,
                detail={"digest": content[:50] + "…"},
            )
            # 拒绝日志先落库再抛(组合根同会话:不 commit 会被异常回滚带走)
            await self._repo.commit()
            raise SensitiveContentRejected(content[:50])
        vector = await self._embed_quietly([content])
        await self._repo.insert(
            user_id=user_id,
            tenant_id=tenant_id,
            content=content,
            category="general",
            confidence=1.0,
            source="EXPLICIT",
            vector=vector,
            team_id=team_id,
        )
        await self._oper.add(
            user_id=user_id, action="memory.create", target=None, detail={"source": "EXPLICIT"}
        )
        await self._repo.commit()  # R1-H1:operlog 同事务落库(add 后必 commit)

    async def form_implicit(
        self, *, user_id: str, tenant_id: str, candidates: list[dict[str, str]]
    ) -> int:
        """隐式落库(后台任务):逐条敏感检测+去重;返回落库条数。"""
        saved = 0
        for c in candidates[:3]:
            content = (c.get("content") or "").strip()[:500]
            if not content or contains_sensitive(content):
                continue
            if await self._repo.exists_identical(
                user_id=user_id, tenant_id=tenant_id, content=content
            ):
                continue
            vector = await self._embed_quietly([content])
            await self._repo.insert(
                user_id=user_id,
                tenant_id=tenant_id,
                content=content,
                category=(c.get("category") or "general")[:32],
                confidence=0.5,
                source="IMPLICIT",
                vector=vector,
            )
            saved += 1
        if saved:
            await self._oper.add(
                user_id=user_id,
                action="memory.create",
                target=None,
                detail={"source": "IMPLICIT", "count": saved},
            )
            await self._repo.commit()  # R1-H1
        return saved

    # ── 自管理(数据主权=本人本租户) ──

    async def list_mine(self, *, user_id: str, tenant_id: str) -> list[dict[str, object]]:
        return await self._repo.list_mine(user_id=user_id, tenant_id=tenant_id)

    async def update_mine(
        self, *, memory_id: str, user_id: str, tenant_id: str, content: str, category: str
    ) -> None:
        if contains_sensitive(content):
            # R1-L5:编辑拒绝同落 operlog(与显式形成拒绝同口径)
            await self._oper.add(
                user_id=user_id,
                action="memory.reject_sensitive",
                target=memory_id,
                detail={"digest": content[:50] + "…"},
            )
            await self._repo.commit()
            raise SensitiveContentRejected(content[:50])
        vector = await self._embed_quietly([content])
        ok = await self._repo.update_mine(
            memory_id=memory_id,
            user_id=user_id,
            tenant_id=tenant_id,
            content=content,
            category=category,
            vector=vector,
        )
        if not ok:
            raise MemoryNotFound(memory_id)
        await self._oper.add(user_id=user_id, action="memory.update", target=memory_id, detail={})
        await self._repo.commit()  # R1-H1

    async def delete_mine(self, *, memory_id: str, user_id: str, tenant_id: str) -> None:
        ok = await self._repo.delete_mine(memory_id=memory_id, user_id=user_id, tenant_id=tenant_id)
        if not ok:
            raise MemoryNotFound(memory_id)
        await self._oper.add(user_id=user_id, action="memory.delete", target=memory_id, detail={})
        await self._repo.commit()  # R1-H1
