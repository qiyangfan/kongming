"""邮件入站用例:按 UID 游标轮询新邮件,UIDVALIDITY 变化时重扫。

执行记录和游标同事务保存;连接或目标执行失败时记录失败状态且不推进
游标,后续轮询可重试;任何异常不逃逸(单触发器失败不扩散)。"""

import datetime as dt
import json
import logging
from typing import Any

from kongming.trigger.adapters import normalize_email
from kongming.trigger.firing import FiringCore
from kongming.trigger.ports import (
    InputGuardrail,
    MailAuthError,
    MailClient,
    MailConnectError,
    MailError,
    MailFetch,
    MailProtocolError,
    MailRawMessage,
)
from kongming.trigger.validation import MAX_MESSAGES_PER_POLL, TASK_TARGET, now

# 服务日志名保持 kongming.trigger.service(拆分模块后外部仍按此名寻址服务日志)
log = logging.getLogger("kongming.trigger.service")

# 拉取异常按端口错误类分类落 FAILED(MailError 为基类,子类先判定;
# 不在表内的异常归 MAIL_ERROR 并带原文截断)
_FETCH_FAILURES = (
    (MailAuthError, "AUTH_FAILED"),
    (MailConnectError, "CONNECT_FAILED"),
    (MailProtocolError, "PROTOCOL_ERROR"),
    (MailError, "PROTOCOL_ERROR"),
)


class TriggerEmailService(FiringCore):
    """邮件轮询:游标续传、输入护栏、task 目标闸与节流推进。

    IMAP 客户端与输入护栏由组合根经 FiringCore 的同一套关键字装配;
    未注入 mail=邮件形态不可轮询(分类落 FAILED)。"""

    # 邮件端口(组合根绑定)
    _mail: MailClient | None = None
    _input_guard: InputGuardrail | None = None

    async def list_due_emails(self, *, now_iso: str) -> list[dict[str, Any]]:
        """调度器调用:轮询间隔到期的邮件触发器(停用即出扫描面)。"""
        return await self._repo.list_due_emails(now_iso=now_iso)

    async def mark_email_polled(self, *, trigger_id: str, interval_sec: int) -> None:
        """轮询节流推进(成功/失败/SKIPPED 共用):last_fired=now,
        next_fire=now+轮询间隔——失败不堆积,下个间隔再试。"""
        current = now()
        await self._repo.mark_fired(
            trigger_id=trigger_id,
            last_fired_iso=current.isoformat(),
            next_fire_iso=(current + dt.timedelta(seconds=max(1, int(interval_sec)))).isoformat(),
        )
        await self._repo.commit()

    async def poll_email(self, *, trigger_id: str) -> None:
        """按 UID 游标处理新邮件；UIDVALIDITY 变化时重扫。执行记录和游标同事务保存。

        连接或目标执行失败时记录失败状态且不推进游标，后续轮询可重试；
        任何异常不逃逸(单触发器失败不扩散)。
        """
        row = await self._repo.get_by_id(trigger_id=trigger_id)
        if row is None or not row["enabled"] or str(row["type"]) != "EMAIL":
            return
        folder = str(row.get("imap_folder") or "INBOX")
        cur = await self._repo.get_email_cursor(trigger_id=trigger_id, folder=folder)
        fetched = await self._fetch_round(row, trigger_id=trigger_id, folder=folder, cur=cur)
        if fetched is None:
            return
        uidvalidity, last_uid, batch = fetched
        for m in batch:
            # IMAP `n:*` 语义:范围端点可反转(无更新时服务器仍回最高 UID
            # 一封)——严格大于游标才处理,幂等防线在调用侧
            if m.uid > last_uid:
                await self._handle_message(
                    row, trigger_id=trigger_id, folder=folder, m=m, uidvalidity=uidvalidity
                )
        # 末封邮件的游标推进收尾提交(_fire 的 commit 先于本封游标
        # upsert,末封游标原依赖调度器节流提交搭车——节流提交失败被吞时
        # close 回滚即丢游标,下轮确定性重复触发末封邮件)
        await self._repo.commit()

    async def _fetch_round(
        self, row: dict[str, Any], *, trigger_id: str, folder: str, cur: dict[str, Any] | None
    ) -> tuple[int, int, list[MailRawMessage]] | None:
        """一次轮询的拉取:凭证解密 → UIDVALIDITY 探测(变更则全量重扫) → 邮件批次。

        返回 (uidvalidity, 处理起点 UID, 升序邮件);前置失败已分类落库,返回 None。"""
        if self._mail is None:
            await self.record_failed(
                tenant_id=str(row["tenant_id"]),
                trigger_id=trigger_id,
                reason="MAIL_ERROR: 邮件客户端未接线(部署缺陷)",
            )
            return None
        credential = await self._decrypt_credential(row, trigger_id=trigger_id)
        if credential is None:
            return None
        mail_host = str(row.get("imap_host") or "")
        mail_port = int(row.get("imap_port") or 993)
        mail_tls = bool(row.get("imap_tls"))
        mail_user = str(row.get("imap_user") or "")
        last_uid = int(cur["last_uid"]) if cur else 0
        try:
            fetch = await self._mail.fetch_since(
                host=mail_host,
                port=mail_port,
                tls=mail_tls,
                user=mail_user,
                credential=credential,
                folder=folder,
                last_uid=last_uid,
                limit=MAX_MESSAGES_PER_POLL,
            )
            if await self._reset_on_uidvalidity(trigger_id, folder, cur, fetch):
                # 游标作废:从 0 全量重扫(登记已改写为 0)
                last_uid = 0
                fetch = await self._mail.fetch_since(
                    host=mail_host,
                    port=mail_port,
                    tls=mail_tls,
                    user=mail_user,
                    credential=credential,
                    folder=folder,
                    last_uid=0,
                    limit=MAX_MESSAGES_PER_POLL,
                )
        except Exception as e:  # noqa: BLE001  # 邮件栈未知异常同样分类收敛,永不逃逸
            await self._record_fetch_failure(str(row["tenant_id"]), trigger_id, e)
            return None
        return fetch.uidvalidity, last_uid, sorted(fetch.messages, key=lambda x: x.uid)

    async def _decrypt_credential(self, row: dict[str, Any], *, trigger_id: str) -> str | None:
        """解密 IMAP 凭证;失败(密钥轮换/密文损坏)分类落库并返回 None。"""
        if not row.get("imap_credential"):
            return ""
        try:
            return str(self._cipher.decrypt(str(row["imap_credential"])))
        except Exception as e:  # noqa: BLE001  # 凭证解密失败同样分类落库,不 500 不逃逸
            await self.record_failed(
                tenant_id=str(row["tenant_id"]),
                trigger_id=trigger_id,
                reason=f"CREDENTIAL_ERROR: {type(e).__name__}",
            )
            return None

    async def _reset_on_uidvalidity(
        self, trigger_id: str, folder: str, cur: dict[str, Any] | None, fetch: MailFetch
    ) -> bool:
        """首次轮询登记游标;UIDVALIDITY 变化时告警并重置;返回是否需全量重扫。"""
        if cur is None:
            # 首次轮询:登记游标行(UIDVALIDITY 基准)
            await self._repo.upsert_email_cursor(
                trigger_id=trigger_id, folder=folder, last_uid=0, uidvalidity=fetch.uidvalidity
            )
            return False
        stored_uv = int(cur["uidvalidity"])
        if stored_uv == fetch.uidvalidity:
            return False
        # UIDVALIDITY 变更:游标作废重置——全文件夹重扫会重复触发,
        # 记录告警人工裁决;宁重复不丢失
        log.warning(
            "trigger %s 文件夹 %s UIDVALIDITY 变更 %s→%s,游标重置全量重扫",
            trigger_id,
            folder,
            stored_uv,
            fetch.uidvalidity,
        )
        await self._repo.upsert_email_cursor(
            trigger_id=trigger_id, folder=folder, last_uid=0, uidvalidity=fetch.uidvalidity
        )
        return True

    async def _record_fetch_failure(self, tenant_id: str, trigger_id: str, exc: Exception) -> None:
        """拉取异常按端口错误类型分类落 FAILED(未知异常归 MAIL_ERROR 带原文截断)。"""
        for cls, prefix in _FETCH_FAILURES:
            if isinstance(exc, cls):
                await self.record_failed(
                    tenant_id=tenant_id, trigger_id=trigger_id, reason=f"{prefix}: {exc}"
                )
                return
        await self.record_failed(
            tenant_id=tenant_id,
            trigger_id=trigger_id,
            reason=f"MAIL_ERROR: {type(exc).__name__}: {str(exc)[:200]}",
        )

    async def _handle_message(
        self,
        row: dict[str, Any],
        *,
        trigger_id: str,
        folder: str,
        m: MailRawMessage,
        uidvalidity: int,
    ) -> None:
        """单封处理:摘要白名单 → 输入护栏 → (task 目标过闸) → 触发/落库 → 游标推进。"""
        tenant_id = str(row["tenant_id"])
        summary = normalize_email(m.data)
        blocked, processed = await self._guard(row, tenant_id, summary)
        await self._record_message(
            row,
            trigger_id=trigger_id,
            tenant_id=tenant_id,
            m=m,
            summary=summary,
            blocked=blocked,
            processed=processed,
        )
        await self._repo.upsert_email_cursor(
            trigger_id=trigger_id, folder=folder, last_uid=m.uid, uidvalidity=uidvalidity
        )
        if blocked:
            await self._repo.commit()  # 拦截路径无 _fire 收尾,显式同事务提交

    async def _record_message(
        self,
        row: dict[str, Any],
        *,
        trigger_id: str,
        tenant_id: str,
        m: MailRawMessage,
        summary: dict[str, str],
        blocked: bool,
        processed: list[str],
    ) -> None:
        """单封执行落库:护栏拦截 → FAILED;task 目标跳过 → SKIPPED;否则触发并收尾提交。"""
        if blocked:
            # 拦截:目标不执行、摘要零留存(恶意内容不落库);游标照推进
            # 防同邮件死循环拦截(邮件已消费,审计在护栏审计表)
            await self._repo.insert_run(
                tenant_id=tenant_id,
                trigger_id=trigger_id,
                status="FAILED",
                error=f"输入护栏拦截: 邮件已消费(UID {m.uid}),目标未执行",
                duration_ms=0,
                target_run_ref="",
                summary="",
            )
            return
        masked = {
            "from": processed[0],
            "subject": processed[1],
            "date": summary["date"],
            "text_preview": processed[2],
        }
        if str(row["target_type"]) == TASK_TARGET:
            # task 目标先过闸;SKIPPED 落库后游标照推进(邮件已消费)
            ok, reason = await self._gate_task_target(row=row, inputs=dict(masked))
            if not ok:
                await self._record_skipped(row=row, reason=reason)
                return
        # 触发记录与游标推进同事务(至少一次语义):_fire 收尾 commit
        await self._fire(
            row,
            inputs=dict(masked),
            channel="email",
            summary_json=json.dumps(masked, ensure_ascii=False),
        )

    async def _guard(
        self, row: dict[str, Any], tenant_id: str, summary: dict[str, str]
    ) -> tuple[bool, list[str]]:
        """输入护栏;未接线时按未拦截、原文未脱敏继续(与既有口径一致)。"""
        texts = [summary["from"], summary["subject"], summary["text_preview"]]
        if self._input_guard is None:
            return False, texts
        return await self._input_guard.check(
            tenant_id=tenant_id, user_id=str(row["created_by"]), texts=texts
        )
