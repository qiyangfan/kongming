"""trigger 路由(薄适配层):/system/triggers/* 管理端点 +
/webhooks/{path} 入站端点 + 调度器/目标执行组合根。

目标执行(TargetRunner):独立会话装配;以创建者身份执行
(权限放大面——节点级防御以创建者逐层复校,立约)。
邮件入站——imaplib 标准库客户端绑定 + 输入护栏绑定(组合根);
凭证任何接口不回显(webhook secret 同口径)。
"""

import asyncio
import datetime as dt
import logging
import re
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kongming.auth.repository import SqlOperLogRepo
from kongming.auth.router import current_user, require_perm
from kongming.chat.repository import ChatRepository
from kongming.config import get_settings
from kongming.datasource.cipher import get_cipher
from kongming.db import get_session, get_sessionmaker
from kongming.trigger.models import CronError, next_fire
from kongming.trigger.ports import (
    EventGateVerdict,
    MailAuthError,
    MailConnectError,
    MailError,
    MailFetch,
    MailProtocolError,
    MailRawMessage,
)
from kongming.trigger.repository import SqlTriggerRepo
from kongming.trigger.scheduler import TriggerScheduler
from kongming.trigger.service import (
    TargetNotFound,
    TriggerKeyTaken,
    TriggerNotFound,
    TriggerService,
    TriggerValidationError,
    WebhookRejected,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system/triggers", tags=["trigger"])
webhook_router = APIRouter(tags=["webhook"])

MANAGE = "system:trigger:manage"


def _bot_question(inputs: dict[str, Any]) -> str:
    """组装 bot 目标指令。邮件摘要仅包含四个白名单字段
    (from/subject/date/text_preview,已过输入护栏)进指令正文;webhook 沿用
    summary/event 提要;cron 无输入=固定职责指令。"""
    if "subject" in inputs or "text_preview" in inputs:
        return (
            f"邮件触发 | 主题:{inputs.get('subject') or '(无主题)'}\n"
            f"{inputs.get('text_preview') or ''}"
        )
    if inputs:
        return str(inputs.get("summary") or inputs.get("event") or "定时触发")
    return "定时触发,请执行你的职责"


class _HttpxNotifier:
    """Notifier 绑定(5s 超时;SSRF 判定复用 http_runner 拒绝段)。"""

    async def check_url(self, url: str) -> None:
        from kongming.workflow.http_runner import assert_url_allowed

        await assert_url_allowed(url)

    async def post(self, *, url: str, body: bytes, signature: str) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=httpx.Timeout(5), trust_env=False) as client:
            resp = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Kongming-Signature": signature,
                },
            )
            resp.raise_for_status()


class _ImaplibMailClient:
    """MailClient 绑定:imaplib 标准库实现,零新依赖;阻塞 IO 经
    线程池隔离事件循环。`UID SEARCH UID <n+1>:*` 游标语义 + STATUS 探测
    UIDVALIDITY;连接/认证/协议错误按端口三类抛出(服务层分类落 FAILED)。"""

    _TIMEOUT_SEC = 10  # 连接/读超时(轮询面不挂调度循环)

    async def fetch_since(
        self,
        *,
        host: str,
        port: int,
        tls: bool,
        user: str,
        credential: str,
        folder: str,
        last_uid: int,
        limit: int,
    ) -> MailFetch:
        return await asyncio.to_thread(
            self._fetch_sync,
            host=host,
            port=port,
            tls=tls,
            user=user,
            credential=credential,
            folder=folder,
            last_uid=last_uid,
            limit=limit,
        )

    def _fetch_sync(
        self,
        *,
        host: str,
        port: int,
        tls: bool,
        user: str,
        credential: str,
        folder: str,
        last_uid: int,
        limit: int,
    ) -> MailFetch:
        import imaplib

        from kongming.workflow.http_runner import assert_host_allowed_sync
        from kongming.workflow.ports import SsrFBlocked

        # SSRF 判定(与同文件 notifier 的 assert_url_allowed 同一拒绝段单源):
        # 用户可配 imap_host 是出站面,私网/元数据地址在连接前关死(分类落
        # CONNECT_FAILED,不成为内网探测 oracle)
        try:
            assert_host_allowed_sync(host, port)
        except SsrFBlocked as e:
            raise MailConnectError(f"目标被拒绝: {e}") from e
        try:
            conn = (
                imaplib.IMAP4_SSL(host, port, timeout=self._TIMEOUT_SEC)
                if tls
                else imaplib.IMAP4(host, port, timeout=self._TIMEOUT_SEC)
            )
        except Exception as e:  # noqa: BLE001  # OSError/ssl/超时统归连接失败
            raise MailConnectError(f"连接失败: {type(e).__name__}") from e
        try:
            try:
                conn.login(user, credential)
            except imaplib.IMAP4.abort as e:
                # 连接中断(abort)先于 error 判定:abort 继承 error,但语义是
                # 服务器掐断/EOF 而非口令错——误标 AUTH_FAILED 会误导排障
                raise MailConnectError(f"连接中断: {type(e).__name__}") from e
            except imaplib.IMAP4.error as e:
                raise MailAuthError(f"认证失败: {e}") from e
            except OSError as e:
                raise MailConnectError(f"连接中断: {type(e).__name__}") from e
            # UIDVALIDITY 探测(STATUS 不扰动选中态;方言差异经正则容错)
            typ, data = conn.status(folder, "(UIDVALIDITY)")
            if typ != "OK" or not data or not data[0]:
                raise MailProtocolError(f"UIDVALIDITY 查询失败: {folder}")
            m = re.search(rb"UIDVALIDITY\s+(\d+)", bytes(data[0]))
            if m is None:
                raise MailProtocolError("UIDVALIDITY 响应不可解析")
            uidvalidity = int(m.group(1))
            typ, _sel = conn.select(folder)
            if typ != "OK":
                raise MailProtocolError(f"文件夹不可选: {folder}")
            typ, data = conn.uid("SEARCH", "UID", f"{last_uid + 1}:*")
            if typ != "OK" or not data:
                raise MailProtocolError("UID SEARCH 失败")
            uids = sorted(int(x) for x in bytes(data[0]).split())
            messages: list[MailRawMessage] = []
            for uid in [u for u in uids if u > last_uid][:limit]:
                typ, fdata = conn.uid("FETCH", str(uid), "(RFC822)")
                if typ != "OK" or not fdata:
                    raise MailProtocolError(f"UID {uid} 拉取失败")
                # imaplib 返回形态 (header, bytes) 元组序列:报文体取元组第二位
                raw = next(
                    (cast("bytes", part[1]) for part in fdata if isinstance(part, tuple)),
                    None,
                )
                if raw is None:
                    raise MailProtocolError(f"UID {uid} 响应缺报文段")
                messages.append(MailRawMessage(uid=uid, data=raw))
            return MailFetch(uidvalidity=uidvalidity, messages=messages)
        except (MailError, imaplib.IMAP4.error) as e:
            if isinstance(e, MailError):
                raise
            if isinstance(e, imaplib.IMAP4.abort):
                # 选中/搜索/拉取阶段的连接中断:归 CONNECT_FAILED 而非协议失败
                # (abort 继承 error,统一分支前先分流)
                raise MailConnectError(f"连接中断: {type(e).__name__}") from e
            # 选中/搜索/拉取阶段的协议态错误(IMAP4.error)统归协议失败
            raise MailProtocolError(f"协议失败: {e}") from e
        except OSError as e:
            raise MailConnectError(f"连接中断: {type(e).__name__}") from e
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001, S110  # 登出失败不影响已取结果,静默收尾
                pass


class _TriggerInputGuardrail:
    """输入护栏端口绑定:邮件摘要=外部内容,进目标执行前过既有
    护栏;独立短会话读配置,审计经 writer 独立事务(与 chat 面同款装配)。"""

    async def check(
        self, *, tenant_id: str, user_id: str, texts: list[str]
    ) -> tuple[bool, list[str]]:
        from kongming.guardrail.repository import (
            SqlGuardrailAuditWriter,
            SqlGuardrailRepo,
        )
        from kongming.guardrail.service import GuardrailService

        async with get_sessionmaker()() as s:
            svc = GuardrailService(SqlGuardrailRepo(s), SqlGuardrailAuditWriter())
            verdict = await svc.evaluate_input(
                trace_id=uuid.uuid4().hex, user_id=user_id, tenant_id=tenant_id, texts=texts
            )
        return verdict.blocked, verdict.processed


def _make_service(session: AsyncSession, actor_id: str | None = None) -> TriggerService:
    settings = get_settings()

    class _Oper:
        async def log(self, *, action: str, target: str, detail: dict[str, object]) -> None:
            await SqlOperLogRepo(session).add(actor_id, action, target, detail)

    class _TargetCheckerImpl:
        """目标存在性(与前端候选同源,与派发期同口径):
        workflow=创建者视角可见集合(归属过滤:个人创建 + 所在团队,
        与 workflow 列表端点同源同参);bot=租户级存在;
        task=模板数据源存在(数据源为租户级资源,归属人可用口径)。"""

        async def target_exists(
            self, *, target_type: str, target_key: str, tenant_id: str, creator_id: str
        ) -> bool:
            if target_type == "task":
                n = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM public.sys_datasource"
                            " WHERE tenant_id=CAST(:t AS uuid) AND datasource_key=:k"
                        ),
                        {"t": tenant_id, "k": target_key},
                    )
                ).scalar_one()
                return int(n) > 0
            if target_type == "workflow":
                from kongming.team.repository import SqlTeamRepo
                from kongming.workflow.repository import SqlWorkflowRepo

                # 团队候选范围与 list_workflows 保持一致(team_ids=角色映射
                # 键集):缺 team_ids 则他人创建的团队 workflow 在候选可选、
                # 提交必拒——checker 与候选必须同源
                roles = await SqlTeamRepo(session).role_map(user_id=creator_id)
                rows = await SqlWorkflowRepo(session).list_latest(
                    tenant_id=tenant_id,
                    user_id=creator_id,
                    team_ids=list(roles.keys()),
                )
                return any(r["wf_key"] == target_key for r in rows)
            from kongming.bot.repository import SqlBotRepo

            return (
                await SqlBotRepo(session).get_by_key(tenant_id=tenant_id, bot_key=target_key)
                is not None
            )

    class _TaskGatewayImpl:
        """task 目标侧查/写(线程去重判定与事件归属;同会话直调任务仓储)。"""

        async def is_open(self, *, task_id: str) -> bool:
            status = (
                await session.execute(
                    text("SELECT status FROM public.sys_agent_task WHERE id=CAST(:i AS uuid)"),
                    {"i": task_id},
                )
            ).scalar()
            return status in ("PLANNING", "RUNNING", "AWAITING_APPROVAL", "PAUSED")

        async def append_thread_event(self, *, task_id: str, event: dict[str, Any]) -> bool:
            from kongming.task.repository import SqlTaskRepo as _Repo

            await _Repo(session).append_task_event(task_id=task_id, event=event)
            return True

    class _EventJudgeImpl:
        """事件立项判读端口绑定(AgentClient 降级契约保证 None=判读不可用)。"""

        async def judge(
            self, *, source: str, summary: str, goal_preview: str
        ) -> EventGateVerdict | None:
            from kongming.chat.router import get_agent_client as _gac

            out = await _gac().judge_event(
                source=source, summary=summary, goal_preview=goal_preview
            )
            if out is None:
                return None
            return EventGateVerdict(verdict=str(out["verdict"]), reason=str(out["reason"]))

    return TriggerService(
        repo=SqlTriggerRepo(session),
        cipher=get_cipher(settings.datasource_encrypt_key),
        runner=_TargetRunnerImpl(),
        notifier=_HttpxNotifier(),
        oper=_Oper(),
        target_checker=_TargetCheckerImpl(),
        # 部署级固定退避(重试次数上限在触发器行,间隔属部署)
        retry_backoff_sec=settings.trigger_retry_backoff_sec,
        # 邮件入站客户端与输入护栏(组合根绑定)
        mail=_ImaplibMailClient(),
        input_guard=_TriggerInputGuardrail(),
        event_judge=_EventJudgeImpl(),
        task_gateway=_TaskGatewayImpl(),
    )


class _TargetRunnerImpl:
    """目标执行装配(独立会话;创建者身份)。"""

    async def run_target(
        self,
        *,
        target_type: str,
        target_key: str,
        tenant_id: str,
        tenant_key: str,
        creator_id: str,
        inputs: dict[str, Any],
        channel: str,
        template: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        async with get_sessionmaker()() as s:
            try:
                # 创建者账号态闸(与 bot 分支对称)——禁用用户
                # 的权限绑定仍在,不经此闸其 cron 工作流持续以其权限执行;
                # 删除用户则绑定消失由六层 fail-closed(既定正确)
                from sqlalchemy import text as _status_text

                creator_status = (
                    await s.execute(
                        _status_text(
                            "SELECT status FROM public.sys_user WHERE id=CAST(:u AS uuid)"
                        ),
                        {"u": creator_id},
                    )
                ).scalar_one_or_none()
                if creator_status != "ACTIVE":
                    return False, "", f"创建者账号不可用(status={creator_status})"
                if target_type == "workflow":
                    # tenant_key 反解(触发器行只有 tenant_id)
                    from sqlalchemy import text as _text

                    from kongming.chat.router import get_agent_client
                    from kongming.workflow.http_runner import HttpNodeRunner
                    from kongming.workflow.repository import SqlWorkflowRepo
                    from kongming.workflow.router import (
                        AgentWorkflowRunner,
                        InternalSqlRunner,
                    )
                    from kongming.workflow.service import WorkflowService

                    tkey = (
                        await s.execute(
                            _text(
                                "SELECT tenant_key FROM public.sys_tenant WHERE id=CAST(:t AS uuid)"
                            ),
                            {"t": tenant_id},
                        )
                    ).scalar_one()
                    # 派发期与 checker 使用相同的可见性规则:创建者及团队
                    # workflow 成员(经 member_roles)可执行;缺此参
                    # 则团队目标 _assert_visible 恒 False,创建成功派发必败
                    from kongming.team.repository import SqlTeamRepo

                    member_roles = await SqlTeamRepo(s).role_map(user_id=creator_id)
                    svc = WorkflowService(
                        repo=SqlWorkflowRepo(s),
                        sql=InternalSqlRunner(s),
                        http=HttpNodeRunner(),
                        agent=AgentWorkflowRunner(s, get_agent_client()),
                    )
                    out = await svc.run(
                        tenant_id=tenant_id,
                        tenant_key=str(tkey),
                        wf_key=target_key,
                        user_id=creator_id,
                        inputs=inputs,
                        version=None,
                        member_roles=member_roles,
                        # cron/webhook 均系统触发,
                        # MANUAL 仅留给人工端点
                        trigger_source="API",
                        trace_id=uuid.uuid4().hex,
                    )
                    # 目标 run 关联回填(run_id 已随响应返回)
                    return (
                        out["status"] == "SUCCESS",
                        str(out.get("run_id") or ""),
                        str(out.get("error") or ""),
                    )
                # task:模板立项(goal 渲染+护栏 → 创建 → 确认门/自动派发)
                if target_type == "task":
                    import datetime as _dt

                    from kongming.config import get_settings as _gs
                    from kongming.task.repository import SqlTaskRepo as _TaskRepo
                    from kongming.task.router import schedule_task_run
                    from kongming.task.service import TaskService as _TaskSvc
                    from kongming.trace import ensure_trace_id as _trace
                    from kongming.trigger.service import render_goal as _render
                    from kongming.uuid7 import uuid7 as _uuid7

                    tpl = template or {}
                    goal = _render(str(tpl.get("goal_template") or ""), inputs)
                    guard = _TriggerInputGuardrail()
                    blocked, _kept = await guard.check(
                        tenant_id=tenant_id, user_id=creator_id, texts=[goal]
                    )
                    if blocked:
                        return False, "", "输入护栏拦截: 事件材料未通过文本护栏,未立项"
                    settings = _gs()
                    task_id = str(_uuid7())
                    repo = _TaskRepo(s)
                    task_svc = _TaskSvc(repo)
                    await task_svc.create(
                        task_id=task_id,
                        tenant_id=tenant_id,
                        user_id=creator_id,
                        goal=goal,
                        datasource_key=str(tpl.get("datasource_key") or ""),
                        session_id=None,
                        trace_root=_trace(),
                        budget=cast("dict[str, Any] | None", tpl.get("budget")),
                        budget_caps={
                            "max_steps": settings.task_budget_max_steps,
                            "max_tokens": settings.task_budget_max_tokens,
                            "max_seconds": settings.task_budget_max_seconds,
                        },
                    )
                    now_iso = _dt.datetime.now(_dt.UTC).isoformat()
                    topo = tpl.get("topology")
                    if isinstance(topo, dict) and topo:
                        # 拓扑声明标记:计划生成后由 apply_plan 消费应用
                        await repo.append_task_event(
                            task_id=task_id,
                            event={
                                "at": now_iso,
                                "kind": "trigger_topology_declared",
                                "topology": topo,
                            },
                        )
                    if not bool(tpl.get("confirm_auto", False)):
                        # 确认门(缺省):落 PAUSED 等归属人恢复,不自动派发
                        await repo.transition_task_system(
                            task_id=task_id,
                            tenant_id=tenant_id,
                            from_status="PLANNING",
                            to_status="PAUSED",
                            fail_reason="触发立项待确认(归属人恢复后派发)",
                            event={"at": now_iso, "kind": "trigger_awaiting_confirm"},
                        )
                    await s.commit()
                    if bool(tpl.get("confirm_auto", False)):
                        await schedule_task_run(task_id)
                    return True, task_id, ""
                # bot
                from kongming.bot.router import get_bot_service as _bot_service_factory
                from kongming.chat.router import get_agent_client as _gac

                bot_svc = _bot_service_factory(s, _gac())
                out = await bot_svc.run(
                    bot_key=target_key,
                    question=_bot_question(inputs),
                    channel=channel,
                    tenant_id_hint=tenant_id,
                )
                return bool(out.get("success")), "", str(out.get("error") or "")
            except Exception as e:  # noqa: BLE001  # 触发执行永不逃逸(run FAILED 落库)
                log.warning("trigger 目标执行异常", exc_info=True)
                return False, "", f"{type(e).__name__}: {str(e)[:300]}"


# 调度器单例(lifespan start/stop 调用)
scheduler = TriggerScheduler(service_factory=None)  # 工厂在 lifespan 注入


async def _scheduler_factory() -> tuple[TriggerService, Any]:
    session = get_sessionmaker()()
    svc = _make_service(session)

    async def _close() -> None:
        await session.close()

    return svc, _close


def start_scheduler() -> None:
    scheduler.attach_factory(_scheduler_factory)  # lifespan 注入点
    scheduler.start()


async def stop_scheduler() -> None:
    await scheduler.stop()


# ── 传输层模型 ──


class TriggerIn(BaseModel):
    tg_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(pattern="^(CRON|WEBHOOK|EMAIL)$")
    target_type: str = Field(pattern="^(workflow|bot|task)$")
    # task 目标不受理 target_key(配置在任务模板);workflow/bot 须非空(service 校验)
    target_key: str = Field(default="", max_length=64)
    cron_expr: str | None = Field(default=None, max_length=64)
    min_interval_sec: int = Field(default=60, ge=0, le=86400)
    adapter: str = Field(default="custom", pattern="^(custom|github)$")
    notify_url: str = Field(default="", max_length=512)
    secret: str = Field(default="", max_length=256)
    # 失败重试次数上限(0–5;0=不重试,缺省)
    max_retries: int = Field(default=0, ge=0, le=5)
    # task 目标任务模板(goal/数据源/预算/拓扑/确认模式/过滤/线程字段)
    task_template: dict[str, Any] | None = None
    # 邮件形态配置(凭证明文只进不出,即刻加密落库)
    imap_host: str = Field(default="", max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_tls: bool = True
    imap_user: str = Field(default="", max_length=255)
    imap_password: str = Field(default="", max_length=256)
    imap_folder: str = Field(default="INBOX", max_length=255)
    imap_poll_interval_sec: int = Field(default=300, ge=1, le=86400)


class TriggerUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    target_type: str = Field(pattern="^(workflow|bot|task)$")
    target_key: str = Field(default="", max_length=64)
    cron_expr: str | None = Field(default=None, max_length=64)
    min_interval_sec: int = Field(default=60, ge=0, le=86400)
    adapter: str = Field(default="custom", pattern="^(custom|github)$")
    notify_url: str = Field(default="", max_length=512)
    # 密钥轮换(空=保持现值;更新 webhook 密钥后旧签名即失效)
    secret: str = Field(default="", max_length=256)
    # 失败重试次数上限(0–5;0=不重试,缺省)
    max_retries: int = Field(default=0, ge=0, le=5)
    # task 目标任务模板(形态或模板任一变更即保存期校验)
    task_template: dict[str, Any] | None = None
    # 邮件形态配置(凭证空框=保持现值,与 webhook secret 同口径)
    imap_host: str = Field(default="", max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_tls: bool = True
    imap_user: str = Field(default="", max_length=255)
    imap_password: str = Field(default="", max_length=256)
    imap_folder: str = Field(default="INBOX", max_length=255)
    imap_poll_interval_sec: int = Field(default=300, ge=1, le=86400)


async def _tenant_or_403(user: dict[str, object], session: AsyncSession, header: str | None) -> str:
    repo = ChatRepository(session)
    uid = str(user["sub"])
    if header is not None:
        tid = await repo.find_bound_tenant(user_id=uid, tenant_key=header)
        if tid is None:
            raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
        return str(tid)
    default = await repo.find_default_tenant(user_id=uid)
    if default is None:
        raise HTTPException(status_code=403, detail="TENANT_FORBIDDEN")
    return str(default[0])


class TriggerOut(BaseModel):
    """触发器列表中的条目。"""

    id: str
    tg_key: str
    name: str
    type: str
    target_type: str
    target_key: str
    cron_expr: str | None = None
    min_interval_sec: int
    webhook_path: str | None = None
    adapter: str
    notify_url: str = ""
    enabled: bool
    has_secret: bool
    last_fired_at: str | None = None
    next_fire_at: str | None = None
    # 失败重试次数上限(0=不重试)
    max_retries: int = 0
    # task 目标任务模板回显(表单回显;非 task 目标为 null)
    task_template: dict[str, Any] | None = None
    # 邮件形态配置(凭证仅掩码口径,永不回显明文)
    imap_host: str = ""
    imap_port: int = 993
    imap_tls: bool = True
    imap_user: str = ""
    imap_folder: str = "INBOX"
    imap_poll_interval_sec: int = 300
    has_imap_credential: bool = False


class TriggerRunOut(BaseModel):
    id: str
    status: str
    error: str | None = None
    duration_ms: int
    target_run_ref: str | None = None
    created_at: str | None = None
    # 重试链:尝试序号 + 首跑关联 + 挂起重试到期时刻
    attempt: int = 1
    retry_of_run_id: str | None = None
    next_retry_at: str | None = None
    # 邮件触发的标准化摘要 JSON(原文不落库,只留白名单摘要)
    summary: str = ""


class TriggerRunsPageOut(BaseModel):
    total: int
    items: list[TriggerRunOut]


def _row_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tg_key": row["tg_key"],
        "name": row["name"],
        "type": row["type"],
        "target_type": row["target_type"],
        "target_key": row["target_key"],
        "cron_expr": row.get("cron_expr"),
        "min_interval_sec": row["min_interval_sec"],
        "webhook_path": (str(row["webhook_path"]) if row.get("webhook_path") else None),
        "adapter": row["adapter"],
        "notify_url": row.get("notify_url") or "",
        "enabled": bool(row["enabled"]),
        "has_secret": bool(row.get("secret_cipher")),
        "last_fired_at": (row["last_fired_at"].isoformat() if row.get("last_fired_at") else None),
        "next_fire_at": (row["next_fire_at"].isoformat() if row.get("next_fire_at") else None),
        "max_retries": int(row.get("max_retries") or 0),
        "task_template": row.get("task_template"),
        # 邮件配置回显(凭证仅掩码口径 has_imap_credential)
        "imap_host": str(row.get("imap_host") or ""),
        "imap_port": int(row.get("imap_port") or 993),
        "imap_tls": bool(row.get("imap_tls")),
        "imap_user": str(row.get("imap_user") or ""),
        "imap_folder": str(row.get("imap_folder") or "INBOX"),
        "imap_poll_interval_sec": int(row.get("imap_poll_interval_sec") or 300),
        "has_imap_credential": bool(row.get("imap_credential")),
    }


# ── 管理端点 ──


class CronPreviewOut(BaseModel):
    """cron 预览(与创建/调度同源的解析计算,前端防抖消费)。"""

    next: list[str]


@router.get("/cron-preview", response_model=CronPreviewOut)
async def cron_preview(
    expr: Annotated[str, Query(max_length=64)],
    user: Annotated[dict[str, object], Depends(current_user)],
    _: Annotated[None, Depends(require_perm(MANAGE))],
) -> CronPreviewOut:
    """接下来 3 次触发时刻(单次看不出日历跳跃类缺陷);
    非法表达式 400 附原文案——与创建期校验同口径,禁第二套 cron 语义。"""
    try:
        fire = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))  # Asia/Shanghai(与服务层同钟)
        fires: list[str] = []
        for _i in range(3):
            fire = next_fire(expr, fire)
            fires.append(fire.isoformat())
    except CronError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return CronPreviewOut(next=fires)


@router.post("", status_code=201, dependencies=[Depends(require_perm(MANAGE))])
async def create_trigger(
    body: TriggerIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session, str(user["sub"]))
    try:
        out = await svc.create(
            tenant_id=tenant_id,
            tg_key=body.tg_key,
            name=body.name,
            type_=body.type,
            target_type=body.target_type,
            target_key=body.target_key,
            cron_expr=body.cron_expr,
            min_interval_sec=body.min_interval_sec,
            adapter=body.adapter,
            notify_url=body.notify_url,
            secret=body.secret,
            created_by=str(user["sub"]),
            max_retries=body.max_retries,
            task_template=body.task_template,
            imap_host=body.imap_host,
            imap_port=body.imap_port,
            imap_tls=body.imap_tls,
            imap_user=body.imap_user,
            imap_password=body.imap_password,
            imap_folder=body.imap_folder,
            imap_poll_interval_sec=body.imap_poll_interval_sec,
        )
        await session.commit()
    except TriggerValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TargetNotFound:
        raise HTTPException(status_code=400, detail="TARGET_NOT_FOUND") from None
    except TriggerKeyTaken:
        raise HTTPException(status_code=409, detail="TRIGGER_KEY_TAKEN") from None
    return out


@router.get("", response_model=list[TriggerOut], dependencies=[Depends(require_perm(MANAGE))])
async def list_triggers(
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> list[dict[str, Any]]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session)
    return [_row_out(r) for r in await svc.list_all(tenant_id=tenant_id)]


@router.put("/{trigger_id}", dependencies=[Depends(require_perm(MANAGE))])
async def update_trigger(
    trigger_id: str,
    body: TriggerUpdateIn,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session, str(user["sub"]))
    try:
        await svc.update(
            trigger_id=trigger_id,
            tenant_id=tenant_id,
            name=body.name,
            target_type=body.target_type,
            target_key=body.target_key,
            cron_expr=body.cron_expr,
            min_interval_sec=body.min_interval_sec,
            adapter=body.adapter,
            notify_url=body.notify_url,
            secret=body.secret,
            max_retries=body.max_retries,
            task_template=body.task_template,
            imap_host=body.imap_host,
            imap_port=body.imap_port,
            imap_tls=body.imap_tls,
            imap_user=body.imap_user,
            imap_password=body.imap_password,
            imap_folder=body.imap_folder,
            imap_poll_interval_sec=body.imap_poll_interval_sec,
        )
        await session.commit()
    except TriggerValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except TargetNotFound:
        raise HTTPException(status_code=400, detail="TARGET_NOT_FOUND") from None
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.post("/{trigger_id}/enable", dependencies=[Depends(require_perm(MANAGE))])
async def enable_trigger(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session, str(user["sub"]))
    try:
        await svc.set_enabled(trigger_id=trigger_id, tenant_id=tenant_id, enabled=True)
        await session.commit()
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.post("/{trigger_id}/disable", dependencies=[Depends(require_perm(MANAGE))])
async def disable_trigger(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session, str(user["sub"]))
    try:
        await svc.set_enabled(trigger_id=trigger_id, tenant_id=tenant_id, enabled=False)
        await session.commit()
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.delete("/{trigger_id}", dependencies=[Depends(require_perm(MANAGE))])
async def delete_trigger(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session, str(user["sub"]))
    try:
        await svc.delete(trigger_id=trigger_id, tenant_id=tenant_id)
        await session.commit()
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {"ok": True}


@router.get(
    "/{trigger_id}/runs",
    response_model=TriggerRunsPageOut,
    dependencies=[Depends(require_perm(MANAGE))],
)
async def page_trigger_runs(
    trigger_id: str,
    user: Annotated[dict[str, object], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_tenant_id: Annotated[str | None, Header()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    tenant_id = await _tenant_or_403(user, session, x_tenant_id)
    svc = _make_service(session)
    try:
        total, rows = await svc.page_runs(
            tenant_id=tenant_id, trigger_id=trigger_id, page=page, size=size
        )
    except TriggerNotFound:
        raise HTTPException(status_code=404, detail="NOT_FOUND") from None
    return {
        "total": total,
        "items": [
            {
                "id": str(r["id"]),
                "status": r["status"],
                "error": r["error"],
                "duration_ms": r["duration_ms"],
                "target_run_ref": r["target_run_ref"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "attempt": int(r.get("attempt") or 1),
                "retry_of_run_id": (
                    str(r["retry_of_run_id"]) if r.get("retry_of_run_id") else None
                ),
                "next_retry_at": (
                    r["next_retry_at"].isoformat() if r.get("next_retry_at") else None
                ),
                # 标准化摘要 JSON(邮件触发;原文不落库)
                "summary": str(r.get("summary") or ""),
            }
            for r in rows
        ],
    }


# ── webhook 入站(公开端点,无 JWT) ──


@webhook_router.post("/webhooks/{path}")
async def webhook_inbound(
    path: str,
    request: Request,
    x_signature: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    import asyncio

    # Content-Length 预检(超限不读 body 进内存)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > 1 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"detail": "PAYLOAD_TOO_LARGE"})
    body = await request.body()
    if len(body) > 1 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"detail": "PAYLOAD_TOO_LARGE"})
    try:
        uuid.UUID(path)
    except ValueError:
        return JSONResponse(status_code=404, content={"detail": "NOT_FOUND"})
    # GitHub 实际签名头 X-Hub-Signature-256: sha256=<hex>,
    # 与 X-Signature 二取一(前缀剥除后同规校验)
    effective_sig = x_signature or ""
    if not effective_sig and x_hub_signature_256:
        effective_sig = x_hub_signature_256.removeprefix("sha256=")
    async with get_sessionmaker()() as s:
        svc = _make_service(s)
        try:
            # 校验同步完成(防探测同形);执行后台异步(202 即返)
            row = await svc.find_webhook_row(path=path)
            if row is None or not row["enabled"]:
                raise WebhookRejected(path)
            svc.verify_signature(row=row, body=body, signature=effective_sig)
            inputs = svc.normalize_inputs(row=row, body=body, event_header=x_github_event or "")
        except WebhookRejected:
            return JSONResponse(status_code=401, content={"detail": "REJECTED"})
        task = asyncio.create_task(_fire_background(dict(row), inputs))
        task.add_done_callback(lambda _t: None)
    return JSONResponse(status_code=202, content={"accepted": True})


async def _fire_background(row: dict[str, Any], inputs: dict[str, Any]) -> None:
    async with get_sessionmaker()() as s:
        svc = _make_service(s)
        try:
            await svc.fire_prepared(row=row, inputs=inputs)
        except Exception:
            log.warning("webhook 后台执行异常", exc_info=True)
