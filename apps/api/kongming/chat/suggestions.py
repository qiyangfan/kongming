"""对话冷启动建议问题用例(039)。

流程(与 spec chat-suggestions 逐条对映):
租户复校 → 数据源可用性预检(default 直通,不可用同形空清单防 key 探测)
→ 缓存命中即返 → 未命中:schema 摘要(不可得即空清单)→ advisory lock
取锁复查(并发防击穿)→ 生成 → 防线校验(单行折叠/长度/条数/去重)→
落库 → 返回。任一环失败一律收敛空清单且不落缓存(下次访问自然重试);
缓存写失败不阻断本次返回(仅日志,自愈=下次重新生成)。

建议问题仅为 UI 呈现文本,本用例不构成任何执行放行依据。
"""

import logging
from collections.abc import Sequence

from kongming.chat.models import Suggestion
from kongming.chat.ports import (
    ChatStoreError,
    SchemaOverviewSource,
    SuggestionRepo,
    SuggestionTenantSource,
    Suggestor,
)
from kongming.chat.service import TenantForbidden
from kongming.datasource.ports import DatasourceChecker

log = logging.getLogger(__name__)

# api 侧防线(agent 侧产出已清洗:单行/≤60 字;回调体不信任,纵深一层)
_MAX_QUESTIONS = 5
_MAX_QUESTION_LEN = 100


def sanitize_questions(raw: Sequence[object]) -> list[str]:
    """api 侧产出防线:非字符串丢弃、单行折叠、空/超长丢弃、去重、封顶。"""
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        q = " ".join(item.split())
        if not q or len(q) > _MAX_QUESTION_LEN or q in out:
            continue
        out.append(q)
        if len(out) >= _MAX_QUESTIONS:
            break
    return out


class SuggestionsService:
    """建议问题获取用例(039)。端口全部组合根注入(013 反转同范式)。"""

    def __init__(
        self,
        *,
        tenants: SuggestionTenantSource,
        suggestions: SuggestionRepo,
        ds_checker: DatasourceChecker,
        overview: SchemaOverviewSource,
        suggestor: Suggestor,
    ) -> None:
        self._tenants = tenants
        self._suggestions = suggestions
        self._ds_checker = ds_checker
        self._overview = overview
        self._suggestor = suggestor

    async def _resolve_tenant(self, user_id: str, tenant_key: str | None) -> tuple[str, str]:
        """语义同 ChatService._resolve_tenant:显式查白名单,缺省取默认租户。"""
        if tenant_key is not None:
            tenant_id = await self._tenants.find_bound_tenant(
                user_id=user_id, tenant_key=tenant_key
            )
            if tenant_id is None:
                raise TenantForbidden(tenant_key)
            return tenant_id, tenant_key
        default = await self._tenants.find_default_tenant(user_id=user_id)
        if default is None:
            raise TenantForbidden("<default>")
        return default

    async def list_suggestions(
        self, *, user_id: str, tenant_key: str | None, datasource_key: str
    ) -> list[str]:
        tenant_id, resolved_key = await self._resolve_tenant(user_id, tenant_key)

        # 可用性预检(008 D8 同款;default 内置直通):不存在/禁用/越属
        # 同形空清单,不触发生成(spec「租户隔离与数据源可用性」)
        if datasource_key != "default" and not await self._ds_checker.is_usable(
            datasource_key, tenant_id
        ):
            return []

        cached = await self._suggestions.find(tenant_id=tenant_id, datasource_key=datasource_key)
        if cached is not None:
            return cached.questions

        digest = await self._overview.overview(
            tenant_key=resolved_key, datasource_key=datasource_key
        )
        if digest is None:
            return []

        # 并发防击穿:同租户同源串行化,取锁后复查(后到者直接命中)
        await self._suggestions.lock(f"{tenant_id}:{datasource_key}")
        cached = await self._suggestions.find(tenant_id=tenant_id, datasource_key=datasource_key)
        if cached is not None:
            # rollback 即释放事务级咨询锁(不留到请求收尾,防长持锁)
            await self._suggestions.rollback()
            return cached.questions

        raw = await self._suggestor.suggest_questions(digest)
        questions = sanitize_questions(raw)
        if not questions:
            return []  # 生成失败不落缓存(下次访问自然重试)

        try:
            await self._suggestions.upsert(
                Suggestion(
                    tenant_id=tenant_id,
                    datasource_key=datasource_key,
                    questions=questions,
                )
            )
            await self._suggestions.commit()
        except ChatStoreError:
            # 缓存写失败不阻断本次返回(仅日志);并发双写由唯一约束兜底
            log.warning("建议缓存落库失败 ds=%s", datasource_key, exc_info=True)
            await self._suggestions.rollback()
        return questions
