"""guardrail 用例编排(020):三层护栏判定 + 配置合并 + fail-closed 收敛。

零框架纪律:不 import fastapi/sqlalchemy/redis;SQL 全在 repository。
fail-closed 铁律(spec):判定不确定(引擎异常/配置读失败)一律按拦截从严,
护栏自身故障永不成为绕过面、永不裸 500。
"""

import logging
from dataclasses import dataclass, field

from kongming.guardrail.models import (
    GuardrailHit,
    RuleConfig,
    evaluate_text,
)
from kongming.guardrail.ports import GuardrailAuditWriter, GuardrailConfigStore
from kongming.guardrail.rules import REGISTRY

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InputVerdict:
    """输入护栏判定结果:blocked 即拦截;processed 为脱敏后文本序列。"""

    blocked: bool
    processed: list[str]  # 与输入 texts 同序(脱敏后)
    hits: list[GuardrailHit] = field(default_factory=list[GuardrailHit])


class GuardrailService:
    def __init__(
        self,
        config_store: GuardrailConfigStore,
        audit_writer: GuardrailAuditWriter,
    ) -> None:
        self._configs_store = config_store
        self._audit = audit_writer

    async def _configs(self) -> list[RuleConfig]:
        """注册表 + 库覆盖合并视图;库读失败 fail-closed=按注册表缺省从严。"""
        try:
            overrides = await self._configs_store.list_overrides()
        except Exception:
            log.error("护栏配置读取失败,fail-closed 按注册表缺省从严", exc_info=True)
            overrides = {}
        out: list[RuleConfig] = []
        for rule in REGISTRY:
            ov = overrides.get(rule.name, {})
            enabled = ov.get("enabled", rule.enabled)
            action = ov.get("action") or rule.action
            threshold = ov.get("threshold", rule.threshold)
            out.append(
                RuleConfig(
                    rule=rule,
                    enabled=bool(enabled),
                    action=action,
                    threshold=threshold,
                )
            )
        return out

    async def _record_hits(
        self,
        *,
        trace_id: str,
        user_id: str | None,
        tenant_id: str,
        gtype: str,
        hits: list[GuardrailHit],
    ) -> None:
        for h in hits:
            await self._audit.record(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                guardrail_type=gtype,
                rule_name=h.rule_name,
                action=h.action,
                content_digest=h.digest,
            )

    async def evaluate_input(
        self,
        *,
        trace_id: str,
        user_id: str | None,
        tenant_id: str,
        texts: list[str],
    ) -> InputVerdict:
        """输入护栏:任一文本命中 blocked 即整体拦截;masked 逐条脱敏。"""
        try:
            configs = await self._configs()
            processed: list[str] = []
            all_hits: list[GuardrailHit] = []
            blocked = False
            for t in texts:
                new_text, hits = evaluate_text(t, configs, "input")
                processed.append(new_text)
                all_hits.extend(hits)
                if any(h.action == "blocked" for h in hits):
                    blocked = True
        except Exception:
            # fail-closed:引擎异常按拦截(spec)
            log.error("输入护栏判定异常,fail-closed 拦截 trace_id=%s", trace_id, exc_info=True)
            hit = GuardrailHit(
                rule_name="__fail_closed__",
                action="blocked",
                digest="输入护栏判定异常(fail-closed)",
            )
            await self._record_hits(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                gtype="input",
                hits=[hit],
            )
            return InputVerdict(blocked=True, processed=texts, hits=[hit])
        await self._record_hits(
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            gtype="input",
            hits=all_hits,
        )
        return InputVerdict(blocked=blocked, processed=processed, hits=all_hits)

    async def evaluate_outputs(
        self,
        *,
        trace_id: str,
        user_id: str | None,
        tenant_id: str,
        texts: list[str],
        safe_replacement: str,
    ) -> list[str]:
        """输出护栏(批量,单次配置读):blocked → 替换安全文案;masked → 脱敏;
        零命中原文。fail-closed:判定异常全部替换安全文案。"""
        try:
            configs = await self._configs()
        except Exception:
            log.error("输出护栏配置异常,fail-closed 替换 trace_id=%s", trace_id, exc_info=True)
            # R1-L6:fail-closed 故障落审计(spec「故障事件落审计」字面立约)
            await self._record_hits(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                gtype="output",
                hits=[
                    GuardrailHit(
                        rule_name="__fail_closed__",
                        action="blocked",
                        digest="输出护栏配置异常(fail-closed 替换)",
                    )
                ],
            )
            return [safe_replacement if t.strip() else t for t in texts]
        out: list[str] = []
        for text in texts:
            if not text.strip():
                out.append(text)
                continue
            try:
                new_text, hits = evaluate_text(text, configs, "output")
            except Exception:
                log.error("输出护栏判定异常,fail-closed 替换 trace_id=%s", trace_id, exc_info=True)
                await self._record_hits(
                    trace_id=trace_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    gtype="output",
                    hits=[
                        GuardrailHit(
                            rule_name="__fail_closed__",
                            action="blocked",
                            digest="输出护栏判定异常(fail-closed 替换)",
                        )
                    ],
                )
                out.append(safe_replacement)
                continue
            await self._record_hits(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                gtype="output",
                hits=hits,
            )
            if any(h.action == "blocked" for h in hits):
                out.append(safe_replacement)
            else:
                out.append(new_text)
        return out

    async def review_tool_intent(
        self,
        *,
        trace_id: str,
        user_id: str,
        tenant_id: str,
        tool_write: bool,
        write_attempts: int,
    ) -> bool:
        """工具意图审核(在参数校验之前):写工具 trace 内尝试数超阈值即拒。

        write_attempts=本次之前的同 trace 写工具调用数(含挂起/执行/拒绝);
        返回 True=放行。fail-closed:判定异常拒。
        """
        if not tool_write:
            return True
        try:
            configs = await self._configs()
            cfg = next((c for c in configs if c.rule.name == "write_burst"), None)
            threshold = (cfg.threshold if cfg and cfg.threshold is not None else 1)
            enabled = cfg.enabled if cfg else True
            if enabled and write_attempts >= threshold:
                await self._record_hits(
                    trace_id=trace_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    gtype="tool_intent",
                    hits=[
                        GuardrailHit(
                            rule_name="write_burst",
                            action="blocked",
                            digest=(
                                f"同 trace 写工具调用第 {write_attempts + 1} 次"
                                f"超阈值 {threshold}(fail-closed 口径拦截)"
                            ),
                        )
                    ],
                )
                return False
            return True
        except Exception:
            log.error("意图审核异常,fail-closed 拒绝 trace_id=%s", trace_id, exc_info=True)
            await self._record_hits(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                gtype="tool_intent",
                hits=[
                    GuardrailHit(
                        rule_name="__fail_closed__",
                        action="blocked",
                        digest="意图审核异常(fail-closed 拒绝)",
                    )
                ],
            )
            return False
