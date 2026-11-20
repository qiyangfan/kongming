"""知识库召回管线:三路召回、闸门、关系扩展、试跑、历史回写与列注释读取。

召回只查询控制面知识库并同时限定租户和数据源;业务数据库内省经仓储端口执行。
管理用例(上传/精修/归属/扫描/重建/覆盖报告)见 rag.admin_service。
caller_team_ids/require_usable_datasource 为两服务共享单点。"""

import logging
from dataclasses import dataclass
from typing import Any

from kongming.rag.cache import ContextCache
from kongming.rag.gate import GateResult, gate_hits
from kongming.rag.models import (
    RECALL_TOP_K,
    DatasourceInvalid,
    HistorySample,
    RagContext,
    RecallHit,
    RelationExpansion,
    build_history_sql,
    build_schema_hint,
    expand_by_relations,
    rrf_fuse,
)
from kongming.rag.ports import (
    EmbeddingError,
    EmbeddingPort,
    MemberTeamsReader,
    RagRepo,
    RelationExpansionPort,
    SuggestionCacheEvictor,
)

log = logging.getLogger(__name__)

_RECALL_TOTAL_CAP = RECALL_TOP_K * 2


@dataclass
class _EmbedSlot:
    """主路召回回填的问句向量(None=embedding 失败,历史路随之跳过)。"""

    vector: list[float] | None = None


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用,同形不区分)。"""


async def caller_team_ids(
    member_teams: MemberTeamsReader | None, caller_user_id: str | None
) -> list[str]:
    """调用者团队集合(可见集合与缓存指纹共用单点);无身份/未注入端口
    为空集合,即纯公共形态。"""
    if not caller_user_id or member_teams is None:
        return []
    return sorted(await member_teams.team_ids_of_user(caller_user_id))


async def require_usable_datasource(repo: RagRepo, tenant_id: str, datasource_key: str) -> None:
    """default 免校验(内置路径);非 default 存在+启用+归属(同形,防探测)。"""
    if datasource_key != "default" and not await repo.tenant.datasource_usable(
        datasource_key, tenant_id
    ):
        raise DatasourceInvalid(datasource_key)


async def resolve_tenant(repo: RagRepo, user_id: str, tenant_key: str | None) -> tuple[str, str]:
    """X-Tenant-Id 解析(白名单复校);返回 (tenant_id, tenant_key)。
    召回/管理两服务共用单点。"""
    if tenant_key is not None:
        bound = await repo.tenant.resolve_bound_tenant(user_id, tenant_key)
        if bound is None:
            raise TenantForbidden(tenant_key)
        return bound
    default = await repo.tenant.resolve_default_tenant(user_id)
    if default is None:
        raise TenantForbidden("<default>")
    return default


class RagService:
    def __init__(
        self,
        repo: RagRepo,
        *,
        embedder: EmbeddingPort,
        cache: ContextCache,
        suggestion_evictor: SuggestionCacheEvictor | None = None,
        gate_settings: Any = None,  # 闸门配置(Settings;None=测试轻量装配,组合根恒注入)
        relation_expander: RelationExpansionPort | None = None,  # 组合根注入
        expand_enabled: bool = False,  # 构造缺省关(未注入端口的调用方零行为)
        expand_max: int = 2,
        member_teams: MemberTeamsReader | None = None,  # 可见集合(未注入=纯公共)
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._cache = cache
        self._suggestion_evictor = suggestion_evictor
        self._gate = gate_settings
        self._relation_expander = relation_expander
        self._expand_enabled = expand_enabled
        self._expand_max = max(0, expand_max)
        self._member_teams = member_teams
        # 按路闸门观测(main=主路 / prior=继承路);recall 消费后
        # 原样下发日志,不静默
        self._last_gate_trace: dict[str, object] = {}

    def _gate_configs(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """取向量与全文闸门参数,两路阈值独立,共用 min_count/mean_window;
        未配置时返回 None。"""
        if self._gate is None:
            return None
        shared: dict[str, Any] = {
            "min_count": int(getattr(self._gate, "rag_gate_min_count", 1)),
            "mean_window": int(getattr(self._gate, "rag_gate_mean_window", 3)),
        }
        vec = {
            "top1_threshold": float(getattr(self._gate, "rag_gate_vector_top1", 0.0)),
            "mean_threshold": float(getattr(self._gate, "rag_gate_vector_mean", 0.0)),
            **shared,
        }
        ft = {
            "top1_threshold": float(getattr(self._gate, "rag_gate_fulltext_top1", 0.0)),
            "mean_threshold": float(getattr(self._gate, "rag_gate_fulltext_mean", 0.0)),
            **shared,
        }
        return vec, ft

    async def _caller_team_ids(self, caller_user_id: str | None) -> list[str]:
        return await caller_team_ids(self._member_teams, caller_user_id)

    async def _require_usable_datasource(self, tenant_id: str, datasource_key: str) -> None:
        await require_usable_datasource(self._repo, tenant_id, datasource_key)

    # ============ 租户上下文(显式查白名单,缺省默认租户) ============

    async def resolve_tenant(self, user_id: str, tenant_key: str | None) -> tuple[str, str]:
        """X-Tenant-Id 解析(白名单复校);返回 (tenant_id, tenant_key)。"""
        return await resolve_tenant(self._repo, user_id, tenant_key)

    # ============ 召回(内部端点) ============

    def _apply_gate(
        self,
        route: str,
        vector_hits: list[RecallHit],
        fulltext_hits: list[RecallHit],
        *,
        knowledge_present: bool = True,
    ) -> tuple[list[RecallHit], list[RecallHit]] | None:
        """按路闸门消费(纯函数管道;恒裁剪形态,未注入=None 零行为)。

        route=main|prior(继承路同样过闸——闸门判据是路级相关性,
        继承路的命中同样是余弦/ts_rank 原生分,"多轮依赖表"不构成
        relevance 豁免:junk 依赖表一样稀释提示词)。
        不过闸整路裁掉(过闸全留,dropped 记 trace 可观测,不静默降质);
        零命中路不投票(空集即空集)。
        """
        configs = self._gate_configs()
        if configs is None:
            return None
        if not knowledge_present:
            # 零知识数据源闸门自动旁路:零命中源于「无知识」而非「低相关」,
            # 裁剪会误杀内省回退可答题;trace 如实标注,不静默
            self._last_gate_trace[route] = {"skipped": "no_knowledge"}
            return None
        vec_cfg, ft_cfg = configs
        vec = gate_hits(vector_hits, **vec_cfg)
        ft = gate_hits(fulltext_hits, **ft_cfg)
        self._last_gate_trace[route] = {
            "vector": vec.verdict.as_dict() if vec.verdict else None,
            "vector_dropped": vec.dropped,
            "fulltext": ft.verdict.as_dict() if ft.verdict else None,
            "fulltext_dropped": ft.dropped,
        }
        return vec.passed, ft.passed

    async def _expand_relations(
        self, *, tenant_id: str, datasource_key: str, keys: list[tuple[str, str]]
    ) -> list[RelationExpansion]:
        """按召回结果补充关联表；未启用、未配置或查询失败时跳过扩展。"""
        if not keys or not self._expand_enabled or self._relation_expander is None:
            return []
        try:
            rows = await self._relation_expander.related_tables(
                tenant_id=tenant_id, datasource_key=datasource_key, tables=keys
            )
        except Exception:  # 关系查询失败时跳过扩展，不影响召回。
            log.warning("关系扩展查询失败(降级无扩展)", exc_info=True)
            return []
        return expand_by_relations(keys, rows, max_expand=self._expand_max)

    def _cache_key(self, question: str, prior: str) -> str:
        """缓存键材料(是否带继承问句决定片段数;派生 hash 留在 cache 内部)。"""
        state = "on" if (self._expand_enabled and self._relation_expander is not None) else "off"
        if prior:
            return f"{question}\x1f{prior}\x1f{state}"
        return f"{question}\x1f{state}"

    async def _embed_or_none(self, text: str, *, degraded_note: str) -> list[float] | None:
        """单条向量化;失败返回 None,由调用方降级纯全文路。"""
        try:
            return (await self._embedder.embed([text]))[0]
        except EmbeddingError:
            log.warning("%s,降级纯全文路", degraded_note)
            return None

    async def _fewshot_sqls(
        self, *, tenant_id: str, datasource_key: str, vector: list[float], visible_teams: list[str]
    ) -> list[str]:
        """同问句候选去重后的 few-shot SQL(优先 ADOPTED 反馈纠错样本)。"""
        # 候选已仅含启用态(FEEDBACK 即 ADOPTED),无反馈样本回退执行成功样本;
        # 保持候选相似度序,去重后取前 K
        candidates = await self._repo.recall.recall_history(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            vector=vector,
            visible_team_ids=visible_teams,  # 伴生:历史路同可见集合
        )
        by_question: dict[str, HistorySample] = {}
        for cand in candidates:
            kept = by_question.get(cand.question)
            if kept is None or (cand.source_type == "FEEDBACK" and kept.source_type != "FEEDBACK"):
                by_question[cand.question] = cand
        return [c.sql_text for c in by_question.values()][:RECALL_TOP_K]

    async def _recall_route(
        self,
        route: str,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        visible_teams: list[str],
        knowledge_present: bool,
        embedded: _EmbedSlot | None = None,  # 主路:回填本次问句向量供历史路复用
    ) -> tuple[list[RecallHit], list[RecallHit]]:
        """单路(向量+全文)召回并过闸;embedding 失败时向量路为空。"""
        vector = await self._embed_or_none(question, degraded_note=f"{route} embedding 失败")
        if embedded is not None:
            embedded.vector = vector
        vector_hits = (
            await self._repo.recall.recall_vector(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                visible_team_ids=visible_teams,
            )
            if vector is not None
            else []
        )
        fulltext_hits = await self._repo.recall.recall_fulltext(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            visible_team_ids=visible_teams,
        )
        # 相关度闸门——各按本路原生分独立判定(vector=余弦相似度/
        # fulltext=ts_rank,量纲不共用);恒裁剪:不过闸整路裁掉后重融合
        gated = self._apply_gate(
            route, vector_hits, fulltext_hits, knowledge_present=knowledge_present
        )
        return gated if gated is not None else (vector_hits, fulltext_hits)

    async def _resolve_scope(
        self, tenant_key: str, datasource_key: str, caller_user_id: str | None
    ) -> tuple[str, list[str]]:
        """解析租户并校验数据源可用,返回 (tenant_id, 调用者可见团队集合)。"""
        tenant_id = await self._repo.tenant.tenant_id_by_key(tenant_key)
        if tenant_id is None:
            raise LookupError(tenant_key)  # router 映射 404
        await self._require_usable_datasource(tenant_id, datasource_key)
        return tenant_id, await self._caller_team_ids(caller_user_id)

    async def _recall_main(
        self,
        tenant_id: str,
        datasource_key: str,
        question: str,
        visible_teams: list[str],
        knowledge_present: bool,
    ) -> tuple[list[RecallHit], list[str]]:
        """主路召回融合并按主路向量召回 few-shot;返回 (融合命中, few-shot SQL)。"""
        embedded = _EmbedSlot()
        main_vector, main_fulltext = await self._recall_route(
            "main",
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            visible_teams=visible_teams,
            knowledge_present=knowledge_present,
            embedded=embedded,
        )
        fused = rrf_fuse([main_vector, main_fulltext])
        # 历史路复用主路已算的向量(与 HEAD 同一次 embed,不重复调用)
        vector = embedded.vector
        fewshot_sqls = (
            await self._fewshot_sqls(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                visible_teams=visible_teams,
            )
            if vector is not None
            else []
        )
        return fused, fewshot_sqls

    async def _recall_with_history(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        prior: str,
        visible_teams: list[str],
        knowledge_present: bool,
    ) -> tuple[list[RecallHit], list[str]]:
        """有继承问句时并入继承路命中的新表;返回 (融合命中, few-shot SQL)。"""
        fused, fewshot_sqls = await self._recall_main(
            tenant_id, datasource_key, question, visible_teams, knowledge_present
        )
        if prior and prior != question:
            # 继承路同样过闸(与主路同一判据同一阈值口径——"多轮依赖
            # 表"不是 relevance 豁免;junk 依赖表一样稀释提示词)
            prior_vector, prior_fulltext = await self._recall_route(
                "prior",
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=prior,
                visible_teams=visible_teams,
                knowledge_present=knowledge_present,
            )
            seen = {(h.schema_name, h.table_name) for h in fused}
            fused = fused + [
                h
                for h in rrf_fuse([prior_vector, prior_fulltext])
                if (h.schema_name, h.table_name) not in seen
            ]
            fused = fused[:_RECALL_TOTAL_CAP]
        return fused, fewshot_sqls

    async def _expand_and_log(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        fused: list[RecallHit],
    ) -> list[tuple[str, str]]:
        """召回键尾附加关系扩展表并写 trace;返回最终取条目用的键序列。"""
        # 关系感知一跳扩展(主召回确定后、fetch_entries 前;尾附加使
        # 扩展天然落在最低优先级,预算超限时先裁扩展、主召回不动)
        recalled_keys = [(h.schema_name, h.table_name) for h in fused]
        expansions = await self._expand_relations(
            tenant_id=tenant_id, datasource_key=datasource_key, keys=recalled_keys
        )
        if expansions:
            # 扩展表与经由关系写入 trace,不静默
            log.info(
                "关系扩展 trace: %s",
                [
                    {
                        "table": f"{e.key[0]}.{e.key[1]}",
                        "via": f"{e.via.get('source_table')}->{e.via.get('target_table')}",
                    }
                    for e in expansions
                ],
            )
            recalled_keys = recalled_keys + [e.key for e in expansions]
        return recalled_keys

    async def _assemble_context(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        visible_teams: list[str],
        fused: list[RecallHit],
        fewshot_sqls: list[str],
    ) -> RagContext:
        """下发闸门 trace、补充关系扩展表并取回条目,组装召回上下文。"""
        if self._last_gate_trace:
            # 闸门观测:各路 verdict(生效阈值/判定归因/命中原生分)与
            # dropped 按路(main/prior)全量下发日志,不静默丢弃
            log.info("召回闸门 trace 观测 notes=%s", self._last_gate_trace)
        recalled_keys = await self._expand_and_log(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            fused=fused,
        )
        entries = await self._repo.recall.fetch_entries(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            keys=recalled_keys,
            visible_team_ids=visible_teams,  # 扩展路兜底同可见集合
        )
        return RagContext(
            schema_hint=build_schema_hint(entries),
            history_sql=build_history_sql(fewshot_sqls),
        )

    async def recall(
        self,
        *,
        tenant_key: str,
        datasource_key: str,
        question: str,
        history: list[str] | None = None,
        caller_user_id: str | None = None,  # 调用者身份(可见集合维度)
    ) -> RagContext:
        """执行向量、全文和历史 SQL 三路召回,结果按 RRF 合并。查询同时过滤
        租户、数据源和团队可见范围;embedding 失败时使用全文结果,且不连接
        业务库。"""
        tenant_id, visible_teams = await self._resolve_scope(
            tenant_key, datasource_key, caller_user_id
        )

        prior = (history or [])[-1].strip() if history else ""
        # 键材料作为 question 参数传入——派生(hash)逻辑留在 cache 内部。
        # 键并入扩展形态(开/关各自缓存空间):否则开形态写入的缓存会在配置
        # 切到关停后的 TTL 窗口内命中,返回含扩展的旧输出;关→开同理,
        # 新能力在 TTL 窗口内静默不生效
        key_material = self._cache_key(question, prior)

        cached = await self._cache.get(
            tenant_id, datasource_key, key_material, team_ids=visible_teams
        )
        if cached is not None:
            return cached

        # 前置(单点,主/继承两路过闸共用):零知识数据源闸门旁路——
        # 「没灌知识」与「召回不相关」对闸门同为零命中,语义不可区分
        knowledge_present = await self._repo.recall.has_knowledge(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        fused, fewshot_sqls = await self._recall_with_history(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            prior=prior,
            visible_teams=visible_teams,
            knowledge_present=knowledge_present,
        )
        ctx = await self._assemble_context(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            visible_teams=visible_teams,
            fused=fused,
            fewshot_sqls=fewshot_sqls,
        )
        # 空结果不写缓存，避免 embedding 恢复后仍在 TTL 内持续返回空提示。
        if ctx.schema_hint or ctx.history_sql:
            await self._cache.set(
                tenant_id, datasource_key, key_material, ctx, team_ids=visible_teams
            )
        return ctx

    async def _probe_recall(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        visible_teams: list[str],
        knowledge_present: bool,
    ) -> tuple[
        list[RecallHit],
        list[RecallHit],
        list[RecallHit],
        GateResult[RecallHit],
        GateResult[RecallHit],
    ]:
        """试跑两路召回并过闸;返回 (向量命中, 全文命中, 融合, 向量闸结果, 全文闸结果)。"""
        vector: list[float] | None = None
        try:
            vector = (await self._embedder.embed([question]))[0]
        except EmbeddingError:
            log.warning("试跑 embedding 失败(降级纯全文路)")
        vector_hits = (
            await self._repo.recall.recall_vector(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                vector=vector,
                visible_team_ids=visible_teams,
            )
            if vector is not None
            else []
        )
        fulltext_hits = await self._repo.recall.recall_fulltext(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            visible_team_ids=visible_teams,
        )
        fused = rrf_fuse([vector_hits, fulltext_hits])
        # 未配置闸门或知识为空时不裁剪，verdict 留空表示未判定。
        configs = self._gate_configs()
        if configs is not None and knowledge_present:
            vec_cfg, ft_cfg = configs
            vec_gate = gate_hits(vector_hits, **vec_cfg)
            ft_gate = gate_hits(fulltext_hits, **ft_cfg)
        else:
            # 未注入(测试轻量装配)或零知识旁路:不判不裁,verdict=None 透出
            vec_gate = GateResult(passed=vector_hits)
            ft_gate = GateResult(passed=fulltext_hits)
        return vector_hits, fulltext_hits, fused, vec_gate, ft_gate

    def _probe_expansion(
        self, expansions: list[RelationExpansion], fused_keys: list[tuple[str, str]]
    ) -> dict[str, object]:
        """试跑关系扩展段(展开表与并入扩展后的融合序列)。"""
        return {
            "enabled": bool(self._expand_enabled and self._relation_expander is not None),
            "tables": [
                {
                    "schema_name": e.key[0],
                    "table_name": e.key[1],
                    "via": {
                        "source_table": e.via.get("source_table"),
                        "target_table": e.via.get("target_table"),
                        "join_keys": e.via.get("join_keys"),
                        "source": e.via.get("source"),
                    },
                }
                for e in expansions
            ],
            "fused_after": [
                {"schema_name": key[0], "table_name": key[1]}
                for key in fused_keys + [e.key for e in expansions]
            ],
        }

    @staticmethod
    def _probe_path(hits: list[RecallHit], gate: GateResult[RecallHit]) -> dict[str, object]:
        """试跑单路命中段(原生分 + 闸门判定结论)。"""
        return {
            "hits": [
                {
                    "schema_name": h.schema_name,
                    "table_name": h.table_name,
                    "score": round(h.score, 6),
                    "source": h.source,
                }
                for h in hits
            ],
            "verdict": gate.verdict.as_dict() if gate.verdict else None,
            "dropped": gate.dropped,
        }

    async def recall_probe(
        self,
        *,
        tenant_key: str,
        datasource_key: str,
        question: str,
        caller_user_id: str | None = None,  # 试跑面同可见集合(公共∪所在团队)
    ) -> dict[str, object]:
        """只读执行召回试跑，返回命中、融合、闸门和关系扩展结果；范围遵循调用者可见权限。"""
        tenant_id, visible_teams = await self._resolve_scope(
            tenant_key, datasource_key, caller_user_id
        )
        # 没有知识条目时跳过闸门，避免把旁路状态误报为相关度不足。
        knowledge_present = await self._repo.recall.has_knowledge(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
        vector_hits, fulltext_hits, fused, vec_gate, ft_gate = await self._probe_recall(
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            question=question,
            visible_teams=visible_teams,
            knowledge_present=knowledge_present,
        )
        # 试跑复用关系扩展逻辑，但不写缓存、配置或审计。
        fused_keys = [(h.schema_name, h.table_name) for h in fused]
        expansions = await self._expand_relations(
            tenant_id=tenant_id, datasource_key=datasource_key, keys=fused_keys
        )
        return {
            "question": question,
            "gate_skipped": None if knowledge_present else "no_knowledge",
            "expansion": self._probe_expansion(expansions, fused_keys),
            "vector": self._probe_path(vector_hits, vec_gate),
            "fulltext": self._probe_path(fulltext_hits, ft_gate),
            "fused": [
                {
                    "schema_name": h.schema_name,
                    "table_name": h.table_name,
                    "score": round(h.score, 6),
                }
                for h in fused
            ],
        }

    # ============ 回写(成功 SQL) ============

    async def writeback(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        caller_user_id: str | None = None,
    ) -> None:
        """成功问答按租户、数据源和问句幂等回写最新 SQL;向量化失败时仍保存
        无向量样本,团队样本只对可见团队成员开放。"""
        if not sql_text.strip():
            return
        vec: list[float] | None = None
        try:
            vec = (await self._embedder.embed([question]))[0]
        except EmbeddingError:
            log.warning("回写 embedding 失败(新行无向量;同题重写保留旧向量)")
        try:
            await self._repo.samples.insert_history(
                tenant_id=tenant_id,
                datasource_key=datasource_key,
                question=question,
                sql_text=sql_text,
                embedding=vec,
                visible_teams=await self._caller_team_ids(caller_user_id),
            )
            await self._repo.commit()
        except Exception:  # 回写永不阻断主流程
            log.warning("query_history 回写失败(不阻断主流程)", exc_info=True)
            await self._repo.rollback()

    async def evict_ctx(self, tenant_id: str, datasource_key: str) -> None:
        """样本准入变更后 ctx 缓存按租户+数据源精确失效(复用语义:
        few-shot 样本进出召回会改变同问句的召回产物,缓存须同步)。"""
        await self._cache.evict(tenant_id, datasource_key)

    # ============ 列注释读取(呈现层列标签) ============

    async def column_comments(
        self, *, tenant_id: str, datasource_key: str
    ) -> dict[str, dict[str, str]]:
        """ColumnCommentReader 端口实现:表名 → 列注释原文(整集一次读,
        单数据源注释集量级小;剥离与映射在 chat 呈现纯函数内)。"""
        return await self._repo.entries.column_comments(
            tenant_id=tenant_id, datasource_key=datasource_key
        )
