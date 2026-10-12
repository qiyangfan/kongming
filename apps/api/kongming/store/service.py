"""store 用例编排(033;104 版本化):上架治理(归属+护栏+版本快照)+
发现+互动+直跑。零框架(宪法 Article II);bot 行读取经组合根注入的
BotReader 端口。

104 上架语义:上架=对当前定义(name/greeting/suggestions/system_prompt/
工具集)创建不可变版本快照,经护栏审核后成为「当前上架版本」(指针);
商店展示与直跑一律消费快照(草稿/货架分离)。快照定型与一致性判定
单源在 kongming.store.snapshot(纯函数)。
"""

import uuid as _uuid
from typing import Any, Protocol, cast

from kongming.store.ports import StoreRepo
from kongming.store.snapshot import build_snapshot, snapshots_equal


class StoreValidationError(Exception):
    """400(分值/问候语形态)。"""


class StoreNotFound(Exception):
    """404(不存在/未上架/版本不存在/跨租户——同形)。"""


class StoreForbidden(Exception):
    """403(非归属者上架/查历史/回滚)。"""


class GuardrailRejected(Exception):
    """上架/回滚内容被护栏 BLOCK(指针不动,fail-safe)。"""


class BotReader(Protocol):
    """Bot 行读取(实现侧 bot 域仓储;store 不 import bot 基础设施)。"""

    async def get_bot(self, *, bot_id: str, tenant_id: str) -> dict[str, Any] | None: ...


class StoreGuardrail(Protocol):
    """上架/回滚内容审核(实现侧 020 护栏)。

    filter_output:返回 (blocked, 处理后文本序列)——输出护栏拦截形态=
    安全文案替换(与原文不同即命中 BLOCK/masked,同拒);
    check_input:输入面判定(回滚重审系统提示词用,与 bot 域同口径)。
    """

    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]: ...

    async def check_input(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]: ...


class StoreRunner(Protocol):
    """商店直跑(实现侧 025 链路);返回 (success, summary, error)——
    R1-S2:summary=应答正文(详情对话框直接展示,不误导「去看对话」)。
    104:definition=当前上架版本快照定型(系统提示词/工具集),直跑消费
    快照而非活定义;None=实现侧回退活定义(兼容形态)。"""

    async def run(
        self,
        *,
        bot_key: str,
        question: str,
        tenant_id: str,
        channel: str,
        definition: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]: ...


class ToolLister(Protocol):
    """求交后实际可用工具集解析(实现侧 bot 域,与运行闸同源单一真理源);
    上架准入闸消费(零工具 Bot 拒上架,货架不摆必败商品)。
    104:在快照工具集上求交(row 由调用方以快照工具集覆写 tools)。"""

    async def resolve_runnable(
        self, *, creator_id: str, tenant_id: str, row: dict[str, Any]
    ) -> list[dict[str, Any]]: ...


class StoreService:
    def __init__(
        self,
        *,
        repo: StoreRepo,
        bot_reader: BotReader,
        tool_lister: ToolLister,
        guardrail: StoreGuardrail | None = None,
        runner: StoreRunner | None = None,
        version_cap: int = 10,
    ) -> None:
        self._repo = repo
        self._bot_reader = bot_reader
        self._tool_lister = tool_lister
        self._guardrail = guardrail
        self._runner = runner
        self._version_cap = version_cap

    # ── 治理(104 版本化上架) ──

    async def publish(
        self,
        *,
        bot_id: str,
        tenant_id: str,
        user_id: str,
        greeting: str,
        suggestions: list[str],
        can_manage: bool,
    ) -> dict[str, object]:
        """上架=创建快照→护栏审核→指针切换;内容一致幂等复用(不增版本)。

        返回 {"idempotent": bool, "version_no": int};审核 BLOCK 时无任何
        落库(快照不成为版本、指针不动,fail-safe)。
        """
        if not can_manage:
            raise StoreForbidden("仅创建者或归属团队 OWNER 可上架")
        row = await self._bot_reader.get_bot(bot_id=bot_id, tenant_id=tenant_id)
        if row is None:
            raise StoreNotFound(bot_id)
        if len(greeting) > 500:
            raise StoreValidationError("greeting 超 500 字符")
        # 快照定型(审核对象=快照,先建后审;定义草稿不进快照)
        raw_tools = row.get("tools")
        snapshot = build_snapshot(
            name=row.get("name"),
            greeting=greeting,
            suggestions=suggestions,
            system_prompt=row.get("system_prompt"),
            tools=cast("list[object]", raw_tools) if isinstance(raw_tools, list) else [],
        )
        # 零工具上架闸:在快照工具集上求交(与运行闸同源求交,fail-closed
        # ——端口未接线即构造失败,不做 None 跳过)
        if not await self._tool_lister.resolve_runnable(
            creator_id=str(row["created_by"]),
            tenant_id=tenant_id,
            row={**row, "tools": snapshot["tools"]},
        ):
            raise StoreValidationError("该助手未配置可用工具,不能上架,请先为 Bot 配置工具")
        # 护栏审核(name/greeting/suggestions;BLOCK 拒上架,指针不动)
        await self._audit_snapshot_texts(snapshot=snapshot, user_id=user_id, tenant_id=tenant_id)
        # 幂等上架:定义与当前上架版本内容一致(规范化后逐项相等)→ 复用,
        # 不产生重复版本;仍恢复货架标记(下架后同内容再上架)
        current = await self._repo.current_version(bot_id=bot_id)
        raw_snap: object = current.get("snapshot") if current else None
        if (
            current is not None
            and isinstance(raw_snap, dict)
            and snapshots_equal(cast("dict[str, object]", raw_snap), snapshot)
        ):
            await self._repo.point_current(
                bot_id=bot_id, version_id=str(current["id"]), user_id=user_id
            )
            return {"idempotent": True, "version_no": int(current["version_no"])}
        # 新版本:Bot 内 version_no 递增(单句原子 MAX+1);并发上架撞号时
        # 直接重试(重执即取新 MAX,评审修复:原「读 MAX+1→裸 INSERT」两步
        # 撞唯一约束 500)
        landed: tuple[str, int] | None = None
        for _ in range(3):
            landed = await self._repo.create_version(
                bot_id=bot_id,
                tenant_id=tenant_id,
                snapshot=snapshot,
                user_id=user_id,
            )
            if landed is not None:
                break
        if landed is None:
            raise StoreValidationError("并发上架冲突,请稍后重试")
        vid, version_no = landed
        await self._repo.point_current(bot_id=bot_id, version_id=vid, user_id=user_id)
        await self._repo.evict_overflow_versions(bot_id=bot_id, cap=self._version_cap)
        return {"idempotent": False, "version_no": version_no}

    async def _audit_snapshot_texts(
        self, *, snapshot: dict[str, object], user_id: str, tenant_id: str
    ) -> None:
        """快照文本面审核(name/greeting/suggestions);命中即 GuardrailRejected。"""
        if self._guardrail is None:
            return
        raw_sug = snapshot.get("suggestions")
        sugg = [str(x) for x in cast("list[object]", raw_sug)] if isinstance(raw_sug, list) else []
        name = str(snapshot.get("name") or "")
        greeting = str(snapshot.get("greeting") or "")
        filtered = await self._guardrail.filter_output(
            trace_id=_uuid.uuid4().hex,
            user_id=user_id,
            tenant_id=tenant_id,
            texts=[name, greeting, *sugg],
        )
        if filtered[0] != name or any(
            f != o for f, o in zip(filtered[1:], [greeting, *sugg], strict=True)
        ):
            raise GuardrailRejected("上架内容未过安全审核")

    async def unpublish(self, *, bot_id: str, tenant_id: str, can_manage: bool) -> None:
        if not can_manage:
            raise StoreForbidden("仅创建者或归属团队 OWNER 可下架")
        row = await self._bot_reader.get_bot(bot_id=bot_id, tenant_id=tenant_id)
        if row is None:
            raise StoreNotFound(bot_id)
        await self._repo.unpublish(bot_id=bot_id)
        await self._repo.commit()

    # ── 版本历史与回滚(104) ──

    async def list_versions(
        self, *, bot_id: str, tenant_id: str, can_manage: bool
    ) -> list[dict[str, Any]]:
        """版本历史(归属者与商店治理同权限口径);新版本在前。"""
        if not can_manage:
            raise StoreForbidden("仅创建者或归属团队 OWNER 可查看版本历史")
        row = await self._bot_reader.get_bot(bot_id=bot_id, tenant_id=tenant_id)
        if row is None:
            raise StoreNotFound(bot_id)
        return await self._repo.list_versions(bot_id=bot_id)

    async def rollback(
        self,
        *,
        bot_id: str,
        tenant_id: str,
        user_id: str,
        version_no: int,
        can_manage: bool,
    ) -> dict[str, object]:
        """回滚=历史版本重新指为当前上架版本(视同上架,按现行护栏重审)。

        BLOCK 拒绝且指针不动(fail-safe);回滚到当前版本为幂等 no-op;
        下架态仅恢复指针、不改变下架状态。
        """
        if not can_manage:
            raise StoreForbidden("仅创建者或归属团队 OWNER 可回滚版本")
        row = await self._bot_reader.get_bot(bot_id=bot_id, tenant_id=tenant_id)
        if row is None:
            raise StoreNotFound(bot_id)
        ver = await self._repo.version_by_no(bot_id=bot_id, version_no=version_no)
        if ver is None:
            raise StoreNotFound(f"版本不存在: {version_no}")
        current = await self._repo.current_version(bot_id=bot_id)
        if current is not None and int(current["version_no"]) == version_no:
            return {"version_no": version_no, "switched": False}
        raw_snap: object = ver.get("snapshot")
        if not isinstance(raw_snap, dict):
            raise StoreValidationError("版本快照损坏,无法回滚")
        snapshot = cast("dict[str, object]", raw_snap)
        # 零工具上架闸同款(评审修复):上架后工具授权/数据源被回收的旧版本,
        # 回滚即把必败版本重新指上货架——与 publish 同源求交,空集拒回滚
        if not await self._tool_lister.resolve_runnable(
            creator_id=str(row["created_by"]),
            tenant_id=tenant_id,
            row={**row, "tools": snapshot.get("tools", [])},
        ):
            raise StoreValidationError("该版本的工具当前不可用,无法回滚,请先恢复工具配置")
        # 现行护栏重审:文本面同上架口径;系统提示词按输入面重审
        # (规则升级后旧内容违规即拦,回滚不构成绕过现行审核的通道)
        await self._audit_snapshot_texts(snapshot=snapshot, user_id=user_id, tenant_id=tenant_id)
        if self._guardrail is not None:
            blocked, _ = await self._guardrail.check_input(
                trace_id=_uuid.uuid4().hex,
                user_id=user_id,
                tenant_id=tenant_id,
                texts=[str(snapshot.get("system_prompt") or "")],
            )
            if blocked:
                raise GuardrailRejected("上架内容未过安全审核")
        await self._repo.point_current(
            bot_id=bot_id,
            version_id=str(ver["id"]),
            user_id=user_id,
            # 下架态回滚仅恢复指针、不改变下架状态(spec;不得复活货架标记)
            restore_shelf=False,
        )
        return {"version_no": version_no, "switched": True}

    # ── 发现 ──

    async def list_store(
        self, *, tenant_id: str, keyword: str, sort: str, user_id: str = ""
    ) -> list[dict[str, Any]]:
        return await self._repo.store_list(
            tenant_id=tenant_id, keyword=keyword.strip()[:64], sort=sort, user_id=user_id
        )

    async def get_detail(self, *, tenant_id: str, bot_key: str, user_id: str) -> dict[str, Any]:
        row = await self._repo.store_get(tenant_id=tenant_id, bot_key=bot_key, user_id=user_id)
        if row is None:
            raise StoreNotFound(bot_key)
        row["my_rating"] = await self._repo.my_rating(user_id=user_id, bot_id=str(row["id"]))
        return row

    # ── 互动 ──

    async def set_favorite(
        self, *, tenant_id: str, bot_key: str, user_id: str, favorited: bool
    ) -> bool:
        row = await self._repo.store_get(tenant_id=tenant_id, bot_key=bot_key)
        if row is None:
            raise StoreNotFound(bot_key)
        out = await self._repo.set_favorite(
            tenant_id=tenant_id,
            user_id=user_id,
            bot_id=str(row["id"]),
            favorited=favorited,
        )
        await self._repo.commit()
        return out

    async def my_favorites(self, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        return await self._repo.my_favorites(tenant_id=tenant_id, user_id=user_id)

    async def rate(self, *, tenant_id: str, bot_key: str, user_id: str, score: int) -> None:
        # isinstance 运行时防线保留(cast 断开静态流分析):标注仅约束静态,
        # 绕过路由层 pydantic 的调用面可能传入 float/bool,删除会放行 3.5 落库(行为变化)
        raw_score = cast(object, score)
        if not isinstance(raw_score, int) or isinstance(raw_score, bool) or not 1 <= raw_score <= 5:
            raise StoreValidationError("评分须为 1-5 整数")
        score = raw_score
        row = await self._repo.store_get(tenant_id=tenant_id, bot_key=bot_key)
        if row is None:
            raise StoreNotFound(bot_key)
        await self._repo.upsert_rating(
            tenant_id=tenant_id, user_id=user_id, bot_id=str(row["id"]), score=raw_score
        )
        await self._repo.commit()

    # ── 直跑(104:消费当前上架版本快照) ──

    async def run(self, *, tenant_id: str, bot_key: str, question: str) -> dict[str, Any]:
        row = await self._repo.store_get(tenant_id=tenant_id, bot_key=bot_key)
        if row is None:
            raise StoreNotFound(bot_key)
        if self._runner is None:
            raise StoreValidationError("运行器未接线(部署缺陷)")
        ok, summary, err = await self._runner.run(
            bot_key=bot_key,
            question=question,
            tenant_id=tenant_id,
            channel="store",
            definition=_snapshot_definition(row),
        )
        if ok:
            # 成功才计数(spec;服务端唯一来源,无客户端上报通道)
            await self._repo.incr_use_count(bot_id=str(row["id"]))
            await self._repo.commit()
        return {"success": ok, "summary": summary, "error": err}


def _snapshot_definition(row: dict[str, Any]) -> dict[str, Any] | None:
    """store_get 行 → 直跑定义覆写(快照系统提示词/工具集);
    快照字段缺失(不变量外)返回 None,实现侧回退活定义(兼容形态)。"""
    sp = row.get("snapshot_system_prompt")
    tools = row.get("tools")
    if not isinstance(sp, str) or not tools or not isinstance(tools, list):
        return None
    return {"system_prompt": sp, "tools": [str(t) for t in cast("list[object]", tools)]}
