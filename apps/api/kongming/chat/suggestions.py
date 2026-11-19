"""对话冷启动建议问题用例：校验租户和数据源，读取或生成缓存，并验证输出格式。失败时返回空清单且不缓存；建议文本只用于界面展示。"""

import logging
from collections.abc import Sequence

from kongming.chat.domain_errors import TenantForbidden
from kongming.chat.models import Suggestion
from kongming.chat.ports import (
    ChatStoreError,
    SchemaOverviewSource,
    SuggestionRepo,
    SuggestionTenantSource,
    Suggestor,
)
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
    """建议问题获取用例。端口依赖由组合根注入。"""

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

        # 可用性预检:数据源不存在/禁用/越属均返回同形空清单,不触发生成
        # (default 内置源直通;spec「租户隔离与数据源可用性」)
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
            # 缓存写入失败只记录日志，不影响响应；并发写入由唯一约束处理。
            log.warning("建议缓存落库失败 ds=%s", datasource_key, exc_info=True)
            await self._suggestions.rollback()
        return questions
