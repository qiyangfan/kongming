"""执行沙箱、写入审计记录并整理供 LLM 使用的结果。

runner 和 writer 通过端口注入;传给 LLM 的投影不包含图片 base64。
"""

from kongming.sandbox.ports import SandboxRunner, SandboxRunWriter


class SandboxService:
    """沙箱执行用例:run → audit → 投影。审计失败不阻断(端口语义)。"""

    def __init__(self, *, runner: SandboxRunner, writer: SandboxRunWriter) -> None:
        self._runner = runner
        self._writer = writer

    async def execute(
        self,
        *,
        code: str,
        data: dict[str, object] | None,
        trace_id: str,
        user_id: str,
        tenant_id: str,
    ) -> dict[str, object]:
        """执行并审计,返回 LLM 投影(status/stdout/error/images_count/…)。"""
        outcome = await self._runner.run(code=code, data=data)
        await self._writer.record(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            code=code,
            outcome=outcome,
        )
        projection: dict[str, object] = {
            "status": outcome.status,
            "stdout": outcome.stdout,
            "error": outcome.error,
            "images_count": len(outcome.images),
            "duration_ms": outcome.duration_ms,
        }
        if outcome.image_note:
            projection["image_note"] = outcome.image_note
        if outcome.result_preview:
            projection["result_preview"] = outcome.result_preview
        if data is not None:
            projection["injected"] = f"已注入 df({outcome.data_rows}×{outcome.data_cols})"
        return projection
