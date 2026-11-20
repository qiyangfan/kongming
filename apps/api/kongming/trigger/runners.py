"""trigger 组合根绑定:出站通知/邮件入站/输入护栏/目标执行。

目标执行(TargetRunner):独立会话装配;以创建者身份执行
(权限放大面——节点级防御以创建者逐层复校,立约)。
凭证任何接口不回显(webhook secret 同口径)。
与域纯 adapters.py(输入归一化,零框架契约)区分:本模块持框架装配。
"""

import asyncio
import contextlib
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, cast

from kongming.db import get_sessionmaker
from kongming.trigger.ports import (
    MailAuthError,
    MailConnectError,
    MailError,
    MailFetch,
    MailProtocolError,
    MailRawMessage,
)

log = logging.getLogger(__name__)


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


class HttpxNotifier:
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


class ImaplibMailClient:
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

        opt = _FetchOpts(
            host=host,
            port=port,
            tls=tls,
            user=user,
            credential=credential,
            folder=folder,
            last_uid=last_uid,
            limit=limit,
            timeout=self._TIMEOUT_SEC,
        )
        conn = _open_connection(opt)
        try:
            _login(conn, user, credential)
            uidvalidity = _probe_uidvalidity(conn, folder)
            messages = _fetch_messages(conn, folder, last_uid=last_uid, limit=limit)
            return MailFetch(uidvalidity=uidvalidity, messages=messages)
        except (MailError, imaplib.IMAP4.error) as e:
            raise _classify_session_error(e) from e
        except OSError as e:
            raise MailConnectError(f"连接中断: {type(e).__name__}") from e
        finally:
            # 登出失败不影响已取结果,静默收尾
            with contextlib.suppress(Exception):
                conn.logout()


@dataclass(frozen=True)
class _FetchOpts:
    """一次拉取的连接参数(超出限界的关键字列表,免逐项透传)。"""

    host: str
    port: int
    tls: bool
    user: str
    credential: str
    folder: str
    last_uid: int
    limit: int
    timeout: float


def _open_connection(opt: _FetchOpts) -> Any:
    """SSRF 判定后建立 IMAP 连接(私网/元数据地址在连接前关死)。"""
    import imaplib

    from kongming.workflow.http_runner import assert_host_allowed_sync
    from kongming.workflow.ports import SsrFBlocked

    # SSRF 判定(与同文件 notifier 的 assert_url_allowed 同一拒绝段单源):
    # 用户可配 imap_host 是出站面,私网/元数据地址在连接前关死(分类落
    # CONNECT_FAILED,不成为内网探测 oracle)
    try:
        assert_host_allowed_sync(opt.host, opt.port)
    except SsrFBlocked as e:
        raise MailConnectError(f"目标被拒绝: {e}") from e
    try:
        if opt.tls:
            return imaplib.IMAP4_SSL(opt.host, opt.port, timeout=opt.timeout)
        return imaplib.IMAP4(opt.host, opt.port, timeout=opt.timeout)
    except Exception as e:  # OSError/ssl/超时统归连接失败
        raise MailConnectError(f"连接失败: {type(e).__name__}") from e


def _login(conn: Any, user: str, credential: str) -> None:
    """登录;abort/error/OSError 分流(abort 先判,见下)。"""
    import imaplib

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


def _probe_uidvalidity(conn: Any, folder: str) -> int:
    """UIDVALIDITY 探测(STATUS 不扰动选中态;方言差异经正则容错)。"""
    typ, data = conn.status(folder, "(UIDVALIDITY)")
    if typ != "OK" or not data or not data[0]:
        raise MailProtocolError(f"UIDVALIDITY 查询失败: {folder}")
    m = re.search(rb"UIDVALIDITY\s+(\d+)", bytes(data[0]))
    if m is None:
        raise MailProtocolError("UIDVALIDITY 响应不可解析")
    return int(m.group(1))


def _fetch_messages(conn: Any, folder: str, *, last_uid: int, limit: int) -> list[MailRawMessage]:
    """选中文件夹后按游标拉取(仅 UID 严格大于游标的前 limit 封)。"""
    typ, _sel = conn.select(folder)
    if typ != "OK":
        raise MailProtocolError(f"文件夹不可选: {folder}")
    typ, data = conn.uid("SEARCH", "UID", f"{last_uid + 1}:*")
    if typ != "OK" or not data:
        raise MailProtocolError("UID SEARCH 失败")
    uids = sorted(int(x) for x in bytes(data[0]).split())
    return [_fetch_one(conn, uid) for uid in [u for u in uids if u > last_uid][:limit]]


def _fetch_one(conn: Any, uid: int) -> MailRawMessage:
    """拉取单封 RFC 822 字节。"""
    typ, fdata = conn.uid("FETCH", str(uid), "(RFC822)")
    if typ != "OK" or not fdata:
        raise MailProtocolError(f"UID {uid} 拉取失败")
    # imaplib 返回形态 (header, bytes) 元组序列:报文体取元组第二位
    raw = next((cast("bytes", part[1]) for part in fdata if isinstance(part, tuple)), None)
    if raw is None:
        raise MailProtocolError(f"UID {uid} 响应缺报文段")
    return MailRawMessage(uid=uid, data=raw)


def _classify_session_error(e: MailError | Exception) -> MailError:
    """会话期异常归类:已是端口错误的原样上抛,其余按 abort/协议失败分流。"""
    import imaplib

    if isinstance(e, MailError):
        return e
    if isinstance(e, imaplib.IMAP4.abort):
        # 选中/搜索/拉取阶段的连接中断:归 CONNECT_FAILED 而非协议失败
        # (abort 继承 error,统一分支前先分流)
        return MailConnectError(f"连接中断: {type(e).__name__}")
    # 选中/搜索/拉取阶段的协议态错误(IMAP4.error)统归协议失败
    return MailProtocolError(f"协议失败: {e}")


class TriggerInputGuardrail:
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


class TargetRunnerImpl:
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
                creator_status = await _creator_status(s, creator_id)
                if creator_status != "ACTIVE":
                    return False, "", f"创建者账号不可用(status={creator_status})"
                run = _TARGET_RUNNERS.get(target_type)
                if run is None:
                    return await _run_bot(s, tenant_id, target_key, inputs, channel)
                return await run(
                    s,
                    tenant_id=tenant_id,
                    target_key=target_key,
                    creator_id=creator_id,
                    inputs=inputs,
                    channel=channel,
                    template=template,
                )
            except Exception as e:  # 触发执行永不逃逸(run FAILED 落库)
                log.warning("trigger 目标执行异常", exc_info=True)
                return False, "", f"{type(e).__name__}: {str(e)[:300]}"


async def _creator_status(s: Any, creator_id: str) -> Any:
    """创建者账号状态(无此用户=None,与禁用同路拒)。"""
    from sqlalchemy import text as _status_text

    return (
        await s.execute(
            _status_text("SELECT status FROM public.sys_user WHERE id=CAST(:u AS uuid)"),
            {"u": creator_id},
        )
    ).scalar_one_or_none()


async def _run_workflow(
    s: Any,
    *,
    tenant_id: str,
    target_key: str,
    creator_id: str,
    inputs: dict[str, Any],
    channel: str,
    template: dict[str, Any] | None,
) -> tuple[bool, str, str]:
    """workflow 目标:创建者身份执行,目标 run 关联回填 run_id。"""
    del channel, template  # workflow 目标不消费渠道/模板
    tkey, member_roles, svc = await _workflow_deps(s, tenant_id=tenant_id, creator_id=creator_id)
    out = await svc.run(
        tenant_id=tenant_id,
        tenant_key=tkey,
        wf_key=target_key,
        user_id=creator_id,
        inputs=inputs,
        version=None,
        member_roles=member_roles,
        # cron/webhook 均系统触发,MANUAL 仅留给人工端点
        trigger_source="API",
        trace_id=uuid.uuid4().hex,
    )
    return (
        bool(out["status"] == "SUCCESS"),
        str(out.get("run_id") or ""),
        str(out.get("error") or ""),
    )


async def _workflow_deps(
    s: Any, *, tenant_id: str, creator_id: str
) -> tuple[str, dict[str, str], Any]:
    """装配 workflow 执行依赖(局部 import 防环):租户键/成员角色/服务。"""
    from sqlalchemy import text as _text

    from kongming.chat.dependencies import get_agent_client
    from kongming.team.repository import SqlTeamRepo
    from kongming.workflow.http_runner import HttpNodeRunner
    from kongming.workflow.repository import SqlWorkflowRepo
    from kongming.workflow.router import AgentWorkflowRunner, InternalSqlRunner
    from kongming.workflow.service import WorkflowService

    # tenant_key 反解(触发器行只有 tenant_id)
    tkey = (
        await s.execute(
            _text("SELECT tenant_key FROM public.sys_tenant WHERE id=CAST(:t AS uuid)"),
            {"t": tenant_id},
        )
    ).scalar_one()
    # 派发期与 checker 使用相同的可见性规则:创建者及团队 workflow 成员
    # (经 member_roles)可执行;缺此参则团队目标 _assert_visible 恒 False,
    # 创建成功派发必败
    member_roles = await SqlTeamRepo(s).role_map(user_id=creator_id)
    svc = WorkflowService(
        repo=SqlWorkflowRepo(s),
        sql=InternalSqlRunner(s),
        http=HttpNodeRunner(),
        agent=AgentWorkflowRunner(s, get_agent_client()),
    )
    return str(tkey), member_roles, svc


async def _run_task(
    s: Any,
    *,
    tenant_id: str,
    target_key: str,
    creator_id: str,
    inputs: dict[str, Any],
    channel: str,
    template: dict[str, Any] | None,
) -> tuple[bool, str, str]:
    """task 目标:模板立项(goal 渲染+护栏 → 创建 → 确认门/自动派发)。"""
    import datetime as dt

    from kongming.task.dispatch import schedule_task_run

    del target_key, channel  # task 目标以模板取代 target_key;渠道仅落 run
    tpl = template or {}
    goal = await _render_task_goal(
        tenant_id=tenant_id, creator_id=creator_id, template=tpl, inputs=inputs
    )
    if goal is None:
        return False, "", "输入护栏拦截: 事件材料未通过文本护栏,未立项"
    task_id, repo = await _create_task(
        s, tenant_id=tenant_id, creator_id=creator_id, goal=goal, template=tpl
    )
    now_iso = dt.datetime.now(dt.UTC).isoformat()
    await _declare_topology(repo, task_id, tpl.get("topology"), now_iso)
    auto = bool(tpl.get("confirm_auto", False))
    if not auto:
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
    if auto:
        await schedule_task_run(task_id)
    return True, task_id, ""


async def _render_task_goal(
    *,
    tenant_id: str,
    creator_id: str,
    template: dict[str, Any],
    inputs: dict[str, Any],
) -> str | None:
    """渲染模板 goal 并过输入护栏;护栏拦截返回 None。"""
    from kongming.trigger.task_target import render_goal as _render

    goal = _render(str(template.get("goal_template") or ""), inputs)
    blocked, _kept = await TriggerInputGuardrail().check(
        tenant_id=tenant_id, user_id=creator_id, texts=[goal]
    )
    return None if blocked else goal


async def _create_task(
    s: Any,
    *,
    tenant_id: str,
    creator_id: str,
    goal: str,
    template: dict[str, Any],
) -> tuple[str, Any]:
    """按模板立项并返回 (任务 id, 协作账本仓储)。"""
    from kongming.config import get_settings as _gs
    from kongming.task.collab_repo import SqlCollabLedgerRepo as _TaskRepo
    from kongming.task.service import TaskService as _TaskSvc
    from kongming.trace import ensure_trace_id as _trace
    from kongming.uuid7 import uuid7 as _uuid7

    settings = _gs()
    task_id = str(_uuid7())
    repo = _TaskRepo(s)
    await _TaskSvc(repo).create(
        task_id=task_id,
        tenant_id=tenant_id,
        user_id=creator_id,
        goal=goal,
        datasource_key=str(template.get("datasource_key") or ""),
        session_id=None,
        trace_root=_trace(),
        budget=cast("dict[str, Any] | None", template.get("budget")),
        budget_caps={
            "max_steps": settings.task_budget_max_steps,
            "max_tokens": settings.task_budget_max_tokens,
            "max_seconds": settings.task_budget_max_seconds,
        },
    )
    return task_id, repo


async def _declare_topology(repo: Any, task_id: str, topo: object, now_iso: str) -> None:
    """拓扑声明标记:计划生成后由 apply_plan 消费应用(未声明则忽略)。"""
    if not isinstance(topo, dict) or not topo:
        return
    await repo.append_task_event(
        task_id=task_id,
        event={"at": now_iso, "kind": "trigger_topology_declared", "topology": topo},
    )


async def _run_bot(
    s: Any, tenant_id: str, target_key: str, inputs: dict[str, Any], channel: str
) -> tuple[bool, str, str]:
    """bot 目标:以创建者可见的 bot 身份问答执行。"""
    from kongming.bot.dependencies import get_bot_service as _bot_service_factory
    from kongming.chat.dependencies import get_agent_client as _gac

    bot_svc = _bot_service_factory(s, _gac())
    out = await bot_svc.run(
        bot_key=target_key,
        question=_bot_question(inputs),
        channel=channel,
        tenant_id_hint=tenant_id,
    )
    return bool(out.get("success")), "", str(out.get("error") or "")


# 目标形态 → 执行实现(未登记形态走 bot 分支,与拆分前兜底口径一致)
_TARGET_RUNNERS = {
    "workflow": _run_workflow,
    "task": _run_task,
}
