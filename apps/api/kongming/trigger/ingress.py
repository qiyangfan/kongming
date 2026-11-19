"""WEBHOOK 入站用例:验签、标准化、task 目标过滤闸与执行派发。"""

import hashlib
import hmac
import json
from typing import Any

from kongming.trigger.adapters import AdapterError, normalize
from kongming.trigger.errors import WebhookRejected
from kongming.trigger.firing import FiringCore, parse_json_body
from kongming.trigger.validation import TASK_TARGET

_MAX_BODY = 1 * 1024 * 1024


class TriggerIngressService(FiringCore):
    """入站与派发:webhook 请求期校验 + 后台执行;task 目标语义闸。"""

    async def find_webhook_row(self, *, path: str) -> dict[str, Any] | None:
        return await self._repo.find_by_webhook_path(path=path)

    def verify_signature(self, *, row: dict[str, Any], body: bytes, signature: str) -> None:
        """HMAC 校验(请求期;compare_digest 防时序)。"""
        try:
            secret = self._cipher.decrypt(str(row["secret_cipher"]))
        except Exception as e:
            # 密文损坏/密钥轮换——同形拒(不 500 泄漏内部态)
            raise WebhookRejected(f"secret 不可用: {type(e).__name__}") from e
        expect = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, signature):
            raise WebhookRejected("signature")

    def normalize_inputs(
        self, *, row: dict[str, Any], body: bytes, event_header: str
    ) -> dict[str, Any]:
        """适配器标准化(请求期;畸形同形拒)。"""
        try:
            payload = parse_json_body(body)
            return normalize(str(row["adapter"]), payload, event_header)
        except (AdapterError, ValueError) as e:
            raise WebhookRejected(f"payload 解析失败: {e}") from e

    async def fire_webhook(
        self, *, path: str, body: bytes, signature: str, event_header: str
    ) -> None:
        """后台执行(路由已在请求期完成校验;此处深防御复验一次)。

        summary_json=标准化 inputs JSON:重试回放载体(fire_retry 沿链首
        行还原),与 email 摘要同列同语义。"""
        if len(body) > _MAX_BODY:
            raise WebhookRejected("body 超 1MB")
        row = await self._repo.find_by_webhook_path(path=path)
        if row is None or not row["enabled"]:
            raise WebhookRejected(path)
        self.verify_signature(row=row, body=body, signature=signature)
        inputs = self.normalize_inputs(row=row, body=body, event_header=event_header)
        await self._fire(
            row,
            inputs=inputs,
            channel="webhook",
            summary_json=json.dumps(inputs, ensure_ascii=False),
        )

    async def fire_prepared(self, *, row: dict[str, Any], inputs: dict[str, Any]) -> None:
        """请求期已校验的直入(后台任务用);task 目标先过闸;summary_json=回放载体。"""
        if str(row["target_type"]) == TASK_TARGET:
            ok, reason = await self._gate_task_target(row=row, inputs=inputs)
            if not ok:
                await self._record_skipped(row=row, reason=reason)
                return
        await self._fire(
            row,
            inputs=inputs,
            channel="webhook",
            summary_json=json.dumps(inputs, ensure_ascii=False),
        )
