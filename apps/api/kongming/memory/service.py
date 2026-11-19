"""memory 用例编排:召回/显式形成/隐式落库/自管理。

零框架纪律:不 import fastapi/sqlalchemy/redis;SQL 全在 repository。
"""

import logging
from typing import Protocol, cast

from kongming.memory import prefs as prefs_mod
from kongming.memory.models import contains_sensitive, extract_explicit_intent
from kongming.memory.ports import Embedder, MemoryOperLogger, MemoryRepo

log = logging.getLogger(__name__)


class MemoryNotFound(Exception):
    """记忆不存在或不属于当前用户+租户(404 同形,防探测)。"""


class SensitiveContentRejected(Exception):
    """PII/凭证形态:拒绝落库(spec「PII 拒绝落库」)。"""


class TeamRoles(Protocol):
    """用户团队角色映射(实现侧 team 域;未注入=仅个人召回)。"""

    async def role_map(self, user_id: str) -> dict[str, str]: ...


def _merge_embedded_slots(raw: dict[str, object], src: dict[str, object]) -> None:
    """兼容抽取结果以 content 承载槽位 JSON 的形态:解析出的槽位并入 raw。"""
    content = str(src.get("content") or "").strip()
    if not content.startswith("{") or not content.endswith("}"):
        return
    import json as _json

    try:
        obj = _json.loads(content)
    except Exception:
        obj = {}
    if isinstance(obj, dict):
        _merge_slot_fields(raw, cast("dict[str, object]", obj))


def _merge_slot_fields(raw: dict[str, object], src: dict[str, object]) -> None:
    """把来源中的合法槽位并入 raw;含敏感内容的槽位整项丢弃(脏记忆不作数)。"""
    for k, v in src.items():
        if k not in prefs_mod.SLOT_KEYS or v in (None, ""):
            continue
        if isinstance(v, str) and contains_sensitive(v):
            continue
        if isinstance(v, list) and any(
            contains_sensitive(str(x)) for x in cast("list[object]", v) if x is not None
        ):
            continue
        raw[k] = v


class MemoryService:
    def __init__(
        self,
        repo: MemoryRepo,
        embedder: Embedder,
        oper_logger: MemoryOperLogger,
        team_roles: TeamRoles | None = None,
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
        """召回注入段(memory_hint):失败/空召回 → 空串(调用方不注入)。
        个人 ∪ 成员团队双路(role_map 经端口注入,未注入=仅个人)。"""
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
        lines: list[str] = []
        for r in rows:
            # NOTE 标注段(显式自由文本),与偏好/事实可区分
            mark = "[note] " if str(r.get("category") or "").lower() == "note" else ""
            lines.append(f"- {mark}{r['content']}")
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
        team_id: str | None = None,  # 团队记忆(成员校验在 router)
        route_slot: bool = True,
    ) -> None:
        """显式形成:敏感拒落;embedding 失败降级无向量(可后续编辑补)。
        team_id 非空=团队记忆(隐式提取维持仅个人,team_id NULL)。
        route_slot=True(聊天「记住…」短路)时槽位语义写 user_prefs、
        其余落 NOTE;管理端 POST /memory 传 False——用户显式建记忆条目,
        不得被槽位分流吞掉而从列表消失。"""
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
        category = "general"
        if route_slot:
            slot_hit = prefs_mod.parse_explicit_slot(content)
            if slot_hit is not None:
                key, val = slot_hit
                # 显式聊天写槽位=人工语义(递增人工版本),与隐式补齐分离
                ok = await self._apply_manual_patch(
                    user_id=user_id, tenant_id=tenant_id, raw={key: val}
                )
                if ok:
                    return
            category = "note"
        vector = await self._embed_quietly([content])
        await self._repo.insert(
            user_id=user_id,
            tenant_id=tenant_id,
            content=content,
            category=category,
            confidence=1.0,
            source="EXPLICIT",
            vector=vector,
            team_id=team_id,
        )
        await self._oper.add(
            user_id=user_id, action="memory.create", target=None, detail={"source": "EXPLICIT"}
        )
        await self._repo.commit()  # operlog 同事务落库(add 后必 commit)

    async def form_implicit(
        self,
        *,
        user_id: str,
        tenant_id: str,
        candidates: list[dict[str, object]],
        base_revision: int | None = None,  # 问答准备期捕获的人工版本快照
    ) -> int:
        """隐式只写槽位 patch,不再落自由文本 user_memory。

        停止 IMPLICIT 自由文本(脏记忆比无记忆更伤);候选按槽位 patch
        合并进 user_prefs;空 patch 不落库。返回写入槽位数。
        base_revision=None(准备期读取失败)→ 整轮放弃(不当作 0)。
        """
        raw: dict[str, object] = {}
        for c in candidates[:3]:
            _merge_slot_fields(raw, c)
            _merge_embedded_slots(raw, c)
        ok = await self.apply_implicit_patch(
            user_id=user_id, tenant_id=tenant_id, raw=raw, base_revision=base_revision
        )
        return 1 if ok else 0

    # ── 自管理(数据主权=本人本租户) ──

    async def list_mine(self, *, user_id: str, tenant_id: str) -> list[dict[str, object]]:
        return await self._repo.list_mine(user_id=user_id, tenant_id=tenant_id)

    async def update_mine(
        self, *, memory_id: str, user_id: str, tenant_id: str, content: str, category: str
    ) -> None:
        if contains_sensitive(content):
            # 编辑拒绝同落 operlog(与显式形成拒绝同口径)
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
        await self._repo.commit()

    async def delete_mine(self, *, memory_id: str, user_id: str, tenant_id: str) -> None:
        ok = await self._repo.delete_mine(memory_id=memory_id, user_id=user_id, tenant_id=tenant_id)
        if not ok:
            raise MemoryNotFound(memory_id)
        await self._oper.add(user_id=user_id, action="memory.delete", target=memory_id, detail={})
        await self._repo.commit()

    # ── user_prefs 固定偏好槽位 ──

    async def get_prefs(self, *, user_id: str, tenant_id: str) -> dict[str, object]:
        return await self._repo.get_prefs(user_id=user_id, tenant_id=tenant_id)

    async def put_prefs(
        self, *, user_id: str, tenant_id: str, raw: dict[str, object]
    ) -> dict[str, object]:
        """人工 PUT:显式 null 删除槽位;省略保留;归一化后既无非空
        设置也无删除指令=真正无操作(不建行/不增版本/不取消在途提取/
        不伪造审计),返回当前配置。"""
        err = prefs_mod.reject_unknown_keys(raw)
        if err:
            raise ValueError(err)
        patch = prefs_mod.normalize_put_patch(raw)
        if patch.is_empty():
            return await self._repo.get_prefs(user_id=user_id, tenant_id=tenant_id)
        merged, _rev = await self._repo.merge_prefs_manual(
            user_id=user_id,
            tenant_id=tenant_id,
            values=patch.values,
            deletes=sorted(patch.deletes),
        )
        await self._oper.add(
            user_id=user_id,
            action="memory.prefs.update",
            target=None,
            detail={"keys": sorted(set(patch.values) | set(patch.deletes))},
        )
        await self._repo.commit()
        return merged

    async def clear_prefs(self, *, user_id: str, tenant_id: str) -> dict[str, object]:
        """清空全部偏好:保留空行且递增版本(防旧任务复活);仅影响
        偏好,笔记/团队记忆不受影响。"""
        prefs, _rev = await self._repo.clear_prefs_manual(user_id=user_id, tenant_id=tenant_id)
        await self._oper.add(user_id=user_id, action="memory.prefs.clear", target=None, detail={})
        await self._repo.commit()
        return prefs

    async def _apply_manual_patch(
        self, *, user_id: str, tenant_id: str, raw: dict[str, object]
    ) -> bool:
        """聊天显式写槽位:人工合并语义;空补丁不落库。"""
        patch = prefs_mod.normalize_put_patch(raw)
        if patch.is_empty():
            return False
        await self._repo.merge_prefs_manual(
            user_id=user_id,
            tenant_id=tenant_id,
            values=patch.values,
            deletes=sorted(patch.deletes),
        )
        await self._oper.add(
            user_id=user_id,
            action="memory.prefs.update",
            target=None,
            detail={"keys": sorted(set(patch.values) | set(patch.deletes))},
        )
        await self._repo.commit()
        return True

    async def apply_implicit_patch(
        self,
        *,
        user_id: str,
        tenant_id: str,
        raw: dict[str, object],
        base_revision: int | None = None,
    ) -> bool:
        """隐式抽取只补缺失槽位:人工版本与快照一致才落,只补缺失、
        不覆盖不删除、不递增人工版本;空 patch 或版本读取失败(None)
        不落库。"""
        if base_revision is None:
            return False
        patch = prefs_mod.extract_slot_patch_from_implicit(raw)
        if patch.is_empty():
            return False
        applied = await self._repo.fill_prefs_if_revision(
            user_id=user_id, tenant_id=tenant_id, values=patch.values, base_revision=base_revision
        )
        if not applied:
            return False
        await self._oper.add(
            user_id=user_id,
            action="memory.prefs.implicit",
            target=None,
            detail={"keys": sorted(patch.values.keys())},
        )
        await self._repo.commit()
        return True

    async def current_revision(self, *, user_id: str, tenant_id: str) -> int | None:
        """问答准备期捕获人工版本快照:读取失败返回 None——调用方
        据此跳过本轮自动写入,不得把未知版本当作 0。"""
        try:
            _prefs, rev = await self._repo.read_prefs_revision(user_id=user_id, tenant_id=tenant_id)
            return rev
        except Exception:
            log.warning("偏好人工版本读取失败(本轮自动写入将跳过)", exc_info=True)
            return None

    async def preference_hint(self, *, user_id: str, tenant_id: str) -> str:
        """偏好注入段(可忽略/不约束数据范围);失败降级空串。"""
        try:
            prefs = await self._repo.get_prefs(user_id=user_id, tenant_id=tenant_id)
            return prefs_mod.build_preference_hint(prefs)
        except Exception:
            log.warning("偏好注入组装失败(降级无注入)", exc_info=True)
            return ""
