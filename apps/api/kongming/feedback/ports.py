"""feedback 域端口(014,typing.Protocol 反转,宪法 Article II)。

FeedbackRepo 镜像 FeedbackRepository 公开方法 + commit/rollback;
service 仅持本端口,不触碰 session(与 013 chat 域同一反转范式)。
"""

from typing import Protocol

from kongming.feedback.models import OverviewData


class FeedbackRepo(Protocol):
    """feedback 持久化端口(014)。

    写路径方法(submit_feedback/handle_status)与 commit 失败抛
    基础设施原异常——本域无容错段(提交/处置失败即 500/映射错误为既定
    语义,与 chat 落库容错场景不同);rollback 不转译。
    """

    # ---- 提交面 ----
    async def find_bound_tenant(self, *, user_id: str, tenant_key: str) -> str | None:
        """显式租户白名单复校(与 chat 域同构)。"""
        ...

    async def find_default_tenant(self, *, user_id: str) -> tuple[str, str] | None:
        """缺省取默认租户:default 优先,否则最早授予。"""
        ...

    async def find_message_for_feedback(
        self, *, message_id: str, user_id: str, tenant_id: str
    ) -> dict[str, object] | None:
        """归属收口:消息 JOIN 会话,user_id+tenant_id 双匹配;
        命中返回 {id, role, session_id, created_at, datasource_key,
        corrected_sql, tenant_key, scope_schema(防火墙边界)},
        否则 None(不存在/越权同形)。"""
        ...

    async def lock_feedback_row(self, *, message_id: str) -> dict[str, object] | None:
        """SELECT ... FOR UPDATE 读现反馈值(rating/comment/corrected_sql),
        并发提交同一消息行锁串行(spec)。"""
        ...

    async def submit_feedback(
        self,
        *,
        message_id: str,
        rating: str,
        comment: str | None,
        corrected_sql: str | None,
    ) -> None:
        """覆盖写反馈列+feedback_time=now+feedback_status=PENDING。"""
        ...

    async def recent_user_question_before(
        self, *, session_id: str, message_created_at: object
    ) -> str | None:
        """同会话内该消息之前最近一条 user 问句(纠错沉淀素材)。"""
        ...

    # ---- 管理面 ----
    async def user_has_scope_all(self, *, user_id: str) -> bool:
        """当前用户角色集是否含 data_scope=ALL(行级可见性判定)。"""
        ...

    async def find_message_for_admin(
        self, *, message_id: str, vis_user_id: str | None, require_feedback: bool = False
    ) -> dict[str, object] | None:
        """处置/涉及表归属:vis_user_id 非 None 时强制本人会话(非 ALL),
        None(ALL)不限;require_feedback=True(处置路径)限定已反馈的
        assistant 消息;命中返回消息全要素,否则 None。"""
        ...

    async def handle_status(
        self, *, message_id: str, status: str, note: str | None, handler_id: str
    ) -> bool:
        """处置落库(status/handler/handle_time/handle_note);未命中 False。"""
        ...

    async def page_feedback(
        self,
        *,
        vis_user_id: str | None,
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
        """明细分页(total, rows);vis_user_id 非 None 强制本人(服务端已覆盖)。"""
        ...

    async def overview_feedback(
        self,
        *,
        vis_user_id: str | None,
        days: int,
        tenant_id: str | None,
        datasource_key: str | None,
    ) -> OverviewData:
        """统计概览(total/up/down/corrected/downRate/trend 逐天补零)。"""
        ...

    async def match_knowledge(
        self, *, tenant_id: str, datasource_key: str, table_names: list[str]
    ) -> list[dict[str, object]]:
        """按 tenant+datasource+table_name 匹配知识库条目(涉及表定位)。"""
        ...

    # ---- 审计/事务 ----
    async def log_oper(
        self, user_id: str | None, action: str, target: str | None, detail: dict[str, object]
    ) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class FeedbackSampleGate(Protocol):
    """070 纠错样本启停窄端口(领域 service 不直接依赖 SQLAlchemy/Redis)。

    promote:ADOPTED 时把纠错 SQL 晋升为可召回样本(含 embedding;
    embedding 不可用 SHALL 抛 PromotionUnavailable——调用方收敛 503,
    与反馈处置同事务,无部分提交);disable:暂停召回(非 ADOPTED/重复
    提交的旧样本;行保留作来源审计,不删)。
    102 伴生:caller_user_id=反馈发起者(样本可见集合归属主体;缺省=公共)。
    """

    async def promote(
        self,
        *,
        tenant_id: str,
        datasource_key: str,
        question: str,
        sql_text: str,
        feedback_message_id: str,
        caller_user_id: str | None = None,
    ) -> None: ...

    async def disable(self, *, feedback_message_id: str) -> None: ...

    async def evict(self, *, tenant_id: str, datasource_key: str) -> None:
        """晋升/停用提交后 ctx 缓存精确失效(租户+数据源;提交后调用)。"""
        ...
