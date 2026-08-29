"""store 用例编排(033):上架治理(归属+护栏)+发现+互动+直跑。
零框架(宪法 Article II);bot 行读取经组合根注入的 BotReader 端口。"""

from typing import Any, Protocol, cast

from kongming.store.ports import StoreRepo


class StoreValidationError(Exception):
    """400(分值/问候语形态)。"""


class StoreNotFound(Exception):
    """404(不存在/未上架/跨租户——同形)。"""


class StoreForbidden(Exception):
    """403(非归属者上架)。"""


class GuardrailRejected(Exception):
    """上架内容被护栏 BLOCK。"""


class BotReader(Protocol):
    """Bot 行读取(实现侧 bot 域仓储;store 不 import bot 基础设施)。"""

    async def get_bot(self, *, bot_id: str, tenant_id: str) -> dict[str, Any] | None: ...


class StoreGuardrail(Protocol):
    """上架内容审核(实现侧 020 护栏输出面);返回 (blocked, 处理后文本序列)。"""

    async def filter_output(
        self, *, trace_id: str, user_id: str, tenant_id: str, texts: list[str]
    ) -> list[str]: ...


class StoreRunner(Protocol):
    """商店直跑(实现侧 025 链路);返回 (success, summary, error)——
    R1-S2:summary=应答正文(详情对话框直接展示,不误导「去看对话」)。"""

    async def run(
        self, *, bot_key: str, question: str, tenant_id: str, channel: str
    ) -> tuple[bool, str, str]: ...


class ToolLister(Protocol):
    """求交后实际可用工具集解析(实现侧 bot 域,与运行闸同源单一真理源);
    上架准入闸消费(零工具 Bot 拒上架,货架不摆必败商品)。"""

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
    ) -> None:
        self._repo = repo
        self._bot_reader = bot_reader
        self._tool_lister = tool_lister
        self._guardrail = guardrail
        self._runner = runner

    # ── 治理 ──

    async def publish(
        self,
        *,
        bot_id: str,
        tenant_id: str,
        user_id: str,
        greeting: str,
        suggestions: list[str],
        can_manage: bool,
    ) -> None:
        if not can_manage:
            raise StoreForbidden("仅创建者或归属团队 OWNER 可上架")
        row = await self._bot_reader.get_bot(bot_id=bot_id, tenant_id=tenant_id)
        if row is None:
            raise StoreNotFound(bot_id)
        # 零工具上架闸:求交后工具集为空的 Bot 拒上架(与运行闸同源求交,
        # fail-closed——端口未接线即构造失败,不做 None 跳过)
        if not await self._tool_lister.resolve_runnable(
            creator_id=str(row["created_by"]), tenant_id=tenant_id, row=row
        ):
            raise StoreValidationError(
                "该助手未配置可用工具,不能上架,请先为 Bot 配置工具"
            )
        if len(greeting) > 500:
            raise StoreValidationError("greeting 超 500 字符")
        suggestions = [str(s)[:200] for s in suggestions[:5]]
        # 上架内容护栏审核(name/greeting/suggestions;BLOCK 拒上架)
        if self._guardrail is not None:
            import uuid as _uuid

            filtered = await self._guardrail.filter_output(
                trace_id=_uuid.uuid4().hex,
                user_id=user_id,
                tenant_id=tenant_id,
                texts=[str(row["name"]), greeting, *suggestions],
            )
            # 输出护栏的拦截形态=安全文案替换(与原文不同即命中 BLOCK)
            if filtered[0] != str(row["name"]) or any(
                f != o for f, o in zip(filtered[1:], [greeting, *suggestions], strict=True)
            ):
                raise GuardrailRejected("上架内容未过安全审核")
        await self._repo.publish(
            bot_id=bot_id, user_id=user_id, greeting=greeting, suggestions=suggestions
        )
        await self._repo.commit()

    async def unpublish(
        self, *, bot_id: str, tenant_id: str, can_manage: bool
    ) -> None:
        if not can_manage:
            raise StoreForbidden("仅创建者或归属团队 OWNER 可下架")
        row = await self._bot_reader.get_bot(bot_id=bot_id, tenant_id=tenant_id)
        if row is None:
            raise StoreNotFound(bot_id)
        await self._repo.unpublish(bot_id=bot_id)
        await self._repo.commit()

    # ── 发现 ──

    async def list_store(
        self, *, tenant_id: str, keyword: str, sort: str, user_id: str = ""
    ) -> list[dict[str, Any]]:
        return await self._repo.store_list(
            tenant_id=tenant_id, keyword=keyword.strip()[:64], sort=sort, user_id=user_id
        )

    async def get_detail(self, *, tenant_id: str, bot_key: str, user_id: str) -> dict[str, Any]:
        row = await self._repo.store_get(
            tenant_id=tenant_id, bot_key=bot_key, user_id=user_id
        )
        if row is None:
            raise StoreNotFound(bot_key)
        row["my_rating"] = await self._repo.my_rating(
            user_id=user_id, bot_id=str(row["id"])
        )
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

    async def rate(
        self, *, tenant_id: str, bot_key: str, user_id: str, score: int
    ) -> None:
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
            tenant_id=tenant_id, user_id=user_id, bot_id=str(row["id"]), score=score
        )
        await self._repo.commit()

    # ── 直跑 ──

    async def run(
        self, *, tenant_id: str, bot_key: str, question: str
    ) -> dict[str, Any]:
        row = await self._repo.store_get(tenant_id=tenant_id, bot_key=bot_key)
        if row is None:
            raise StoreNotFound(bot_key)
        if self._runner is None:
            raise StoreValidationError("运行器未接线(部署缺陷)")
        ok, summary, err = await self._runner.run(
            bot_key=bot_key, question=question, tenant_id=tenant_id, channel="store"
        )
        if ok:
            # 成功才计数(spec;服务端唯一来源,无客户端上报通道)
            await self._repo.incr_use_count(bot_id=str(row["id"]))
            await self._repo.commit()
        return {"success": ok, "summary": summary, "error": err}
