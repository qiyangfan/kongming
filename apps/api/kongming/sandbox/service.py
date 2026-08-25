"""sandbox 用例编排(027):执行 + 审计落库 + LLM 投影组装。

零框架(宪法 Article II):runner/writer 皆端口注入。返回值为 LLM 投影
(工具结果 JSON 内容)——不含 images base64(design D2 修正案:agent
观察截断 2000,base64 只烧预算);完整 SandboxOutcome 为域内事实,
经端口实现的实现侧仍可取(本用例的消费面只要投影)。
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
