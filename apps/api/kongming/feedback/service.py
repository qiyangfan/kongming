"""处理反馈提交、审核处置、关联表分析和统计查询。"""

import logging
from dataclasses import dataclass, field

from kongming.feedback.models import (
    VALID_HANDLE_STATUS,
    OverviewData,
    extract_table_names,
    merge_text,
)
from kongming.feedback.ports import FeedbackRepo, FeedbackSampleGate
from kongming.sqlfw.firewall import check, check_reserved_tables, check_schema_scope

log = logging.getLogger(__name__)


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用——同形不区分,与 chat 域同构)。"""


class FeedbackForbidden(Exception):
    """提交面归属失败(消息不存在/跨用户/跨租户——403 同形不区分,feedback spec)。"""


class MessageNotAssistant(Exception):
    """仅 assistant 消息可反馈(user 消息 400)。"""


class InvalidCorrectedSql(Exception):
    """correctedSql 未过 SQL 防火墙(400,整体拒:不落反馈列不写 query_history)。"""


class SamplePromotionUnavailable(Exception):
    """样例晋升失败;反馈处置整体回滚并返回 503。"""


class InvalidHandleStatus(Exception):
    """处置状态非法(仅 PENDING/ADOPTED/REJECTED/DISMISSED,400)。"""


class FeedbackNotFound(Exception):
    """管理面目标不存在或数据范围外(404 同形,处置/涉及表)。"""


@dataclass(frozen=True)
class SubmitState:
    """提交后当前反馈状态(三态合并后的落库真值,前端激活态回显用)。"""

    rating: str
    comment: str | None
    corrected_sql: str | None


@dataclass(frozen=True)
class InvolvedTable:
    table_name: str
    schema_name: str
    datasource_key: str
    has_knowledge: bool
    knowledge_id: str | None = None


@dataclass(frozen=True)
class InvolvedTablesView:
    tables: list[InvolvedTable] = field(default_factory=list[InvolvedTable])
    parse_error: bool = False


class FeedbackService:
    def __init__(
        self,
        repo: FeedbackRepo,
        sample_gate: FeedbackSampleGate | None = None,  # 审核后晋升/停用(窄端口)
    ) -> None:
        self._repo = repo
        self._sample_gate = sample_gate

    async def _resolve_tenant(self, user_id: str, tenant_key: str | None) -> tuple[str, str]:
        """X-Tenant-Id 解析(与 chat 域同构):显式传入查白名单;缺省取默认租户。"""
        if tenant_key is not None:
            tenant_id = await self._repo.find_bound_tenant(user_id=user_id, tenant_key=tenant_key)
            if tenant_id is None:
                raise TenantForbidden(tenant_key)
            return tenant_id, tenant_key
        default = await self._repo.find_default_tenant(user_id=user_id)
        if default is None:
            raise TenantForbidden("<default>")
        return default

    async def _visibility_user_id(self, actor_id: str) -> str | None:
        """行级可见性(spec):角色集含 ALL → None(不限);否则强制本人。"""
        if await self._repo.user_has_scope_all(user_id=actor_id):
            return None
        return actor_id

    # ============ 反馈提交(feedback spec) ============

    async def submit(
        self,
        *,
        user_id: str,
        tenant_key: str | None,
        message_id: str,
        rating: str,
        comment: str | None,
        corrected_sql: str | None,
    ) -> SubmitState:
        tenant_id, _ = await self._resolve_tenant(user_id, tenant_key)
        msg = await self._repo.find_message_for_feedback(
            message_id=message_id, user_id=user_id, tenant_id=tenant_id
        )
        if msg is None:
            raise FeedbackForbidden(message_id)
        if str(msg["role"]) != "ASSISTANT":
            raise MessageNotAssistant(message_id)

        # correctedSql 防火墙校验(先行:失败整体拒,不锁行不更新,spec「校验失败整体拒绝」)
        if corrected_sql:
            self._check_corrected_sql(corrected_sql, scope_schema=str(msg["scope_schema"]))

        row = await self._repo.lock_feedback_row(message_id=message_id)
        if row is None:  # 竞态兜底:归属查询后被并发删除
            raise FeedbackForbidden(message_id)
        merged_comment = merge_text(
            None if row["feedback_comment"] is None else str(row["feedback_comment"]), comment
        )
        merged_sql = merge_text(
            None if row["corrected_sql"] is None else str(row["corrected_sql"]), corrected_sql
        )

        # 重新提交已晋升的反馈时，在同一事务中停用旧样本后再写入新状态。
        prior_status = None if row["feedback_status"] is None else str(row["feedback_status"])
        if prior_status == "ADOPTED" and self._sample_gate is not None:
            await self._sample_gate.disable(feedback_message_id=message_id)

        await self._repo.submit_feedback(
            message_id=message_id, rating=rating, comment=merged_comment, corrected_sql=merged_sql
        )
        await self._repo.log_oper(user_id, "feedback.submit", message_id, {"rating": rating})
        await self._repo.commit()

        # 停用旧样本后 ctx 缓存按租户+数据源精确失效
        # (该问句的召回产物已变——存量缓存含被停用的纠错样本)
        if prior_status == "ADOPTED" and self._sample_gate is not None:
            await self._sample_gate.evict(
                tenant_id=tenant_id, datasource_key=str(msg["datasource_key"])
            )

        # 提交只记录反馈;样本在审核状态变为 ADOPTED 时晋升。
        return SubmitState(rating=rating, comment=merged_comment, corrected_sql=merged_sql)

    @staticmethod
    def _check_corrected_sql(sql_text: str, *, scope_schema: str) -> None:
        """纠错 SQL 过与问答入口相同的防火墙规则(只读根/保留表/危险函数/schema),不执行。"""
        for verdict in (
            check(sql_text),
            check_reserved_tables(sql_text),
            check_schema_scope(sql_text, {scope_schema, "public"}),
        ):
            if not verdict.passed:
                raise InvalidCorrectedSql(verdict.reason)

    # ============ 处置(feedback-refine spec) ============

    async def handle(
        self, *, actor_id: str, message_id: str, status: str, note: str | None
    ) -> str | None:
        """处置落库;返回三态合并后的 note 真值(前端回显用)。"""
        if status not in VALID_HANDLE_STATUS:
            raise InvalidHandleStatus(status)
        vis = await self._visibility_user_id(actor_id)
        msg = await self._repo.find_message_for_admin(
            message_id=message_id, vis_user_id=vis, require_feedback=True
        )
        if msg is None:
            raise FeedbackNotFound(message_id)
        merged_note = merge_text(
            None if msg["feedback_handle_note"] is None else str(msg["feedback_handle_note"]),
            note,
        )
        ok = await self._repo.handle_status(
            message_id=message_id, status=status, note=merged_note, handler_id=actor_id
        )
        if not ok:  # 竞态兜底
            raise FeedbackNotFound(message_id)
        # 样本准入与处置状态、操作日志同事务(无部分提交);
        # ADOPTED + 非空 correctedSql → 晋升(embedding 失败上抛 →
        # SamplePromotionUnavailable → 503 整体回滚);其余状态 → 停用
        if self._sample_gate is not None:
            corrected = None if msg["corrected_sql"] is None else str(msg["corrected_sql"])
            if status == "ADOPTED" and corrected and corrected.strip():
                await self._promote(msg, tenant_id=str(msg["tenant_id"]), corrected_sql=corrected)
            else:
                await self._sample_gate.disable(feedback_message_id=message_id)
        await self._repo.log_oper(actor_id, "feedback.handle", message_id, {"status": status})
        await self._repo.commit()

        # 晋升/停用提交后 ctx 缓存精确失效(复用既有机制)
        if self._sample_gate is not None:
            await self._sample_gate.evict(
                tenant_id=str(msg["tenant_id"]), datasource_key=str(msg["datasource_key"])
            )
        return merged_note

    async def _promote(self, msg: dict[str, object], *, tenant_id: str, corrected_sql: str) -> None:
        """把纠正 SQL 晋升为样本;缺少关联问句时跳过,embedding 失败时抛晋升异常。"""
        if self._sample_gate is None:
            return
        question = await self._repo.recent_user_question_before(
            session_id=str(msg["session_id"]), message_created_at=msg["created_at"]
        )
        if not question:
            log.warning("ADOPTED 但无同会话 user 问句,跳过样本晋升 message=%s", msg["id"])
            return
        try:
            await self._sample_gate.promote(
                tenant_id=tenant_id,
                datasource_key=str(msg["datasource_key"]),
                question=question,
                sql_text=corrected_sql,
                feedback_message_id=str(msg["id"]),
                # 伴生:样本可见性归属反馈发起者(纠错 SQL 的来源主体)
                caller_user_id=str(msg["user_id"] or "") or None,
            )
        except SamplePromotionUnavailable:
            raise
        except Exception as e:  # noqa: BLE001  # 晋升操作异常统一返回 503，事务由调用方管理。
            raise SamplePromotionUnavailable(str(e)) from e

    # ============ 涉及表定位(feedback-refine spec) ============

    async def involved_tables(self, *, actor_id: str, message_id: str) -> InvolvedTablesView:
        vis = await self._visibility_user_id(actor_id)
        msg = await self._repo.find_message_for_admin(message_id=message_id, vis_user_id=vis)
        if msg is None:
            raise FeedbackNotFound(message_id)
        tables, parse_error = extract_table_names(
            str(msg["sql_text"] or ""), str(msg["corrected_sql"] or "")
        )
        matched = await self._repo.match_knowledge(
            tenant_id=str(msg["tenant_id"]),
            datasource_key=str(msg["datasource_key"]),
            table_names=sorted(tables),
        )
        by_name = {str(m["table_name"]): m for m in matched}
        ds_key = str(msg["datasource_key"])
        views = [
            InvolvedTable(
                table_name=name,
                schema_name=(str(by_name[name]["schema_name"]) if name in by_name else ""),
                datasource_key=ds_key,
                has_knowledge=name in by_name,
                knowledge_id=(str(by_name[name]["id"]) if name in by_name else None),
            )
            for name in sorted(tables)
        ]
        return InvolvedTablesView(tables=views, parse_error=parse_error)

    # ============ 统计/分页(feedback-analytics spec) ============

    async def overview(
        self,
        *,
        actor_id: str,
        days: int,
        tenant_id: str | None,
        datasource_key: str | None,
    ) -> OverviewData:
        vis = await self._visibility_user_id(actor_id)
        return await self._repo.overview_feedback(
            vis_user_id=vis, days=days, tenant_id=tenant_id, datasource_key=datasource_key
        )

    async def page(
        self,
        *,
        actor_id: str,
        user_id: str | None,
        rating: str | None,
        tenant_id: str | None,
        datasource_key: str | None,
        feedback_status: str | None,
        only_corrected: bool,
        start_time: object | None,
        end_time: object | None,
        page: int,
        size: int,
    ) -> tuple[int, list[dict[str, object]]]:
        vis = await self._visibility_user_id(actor_id)
        # userId 仅 ALL 时生效;非 ALL 由 vis 强制本人(筛选传值被覆盖,spec)
        effective_uid = user_id if vis is None else None
        return await self._repo.page_feedback(
            vis_user_id=vis,
            user_id=effective_uid,
            rating=rating,
            tenant_id=tenant_id,
            datasource_key=datasource_key,
            feedback_status=feedback_status,
            only_corrected=only_corrected,
            start_time=start_time,
            end_time=end_time,
            page=page,
            size=size,
        )
