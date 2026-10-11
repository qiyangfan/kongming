"""connector 用例编排(099):注册校验→护栏闸→加密落库→掩码查询面→注入
装配→测试调用。零框架纪律(宪法 Article II);SQL 全在 repository,凭证
加密/护栏/审计/出站经 ports 端口注入,组合根在 router。

凭证口径(spec「凭证不回显」):任何接口响应只含鉴权形态与尾号掩码——
创建/更新响应亦不回显;明文仅在落库前(加密)与出站装配时瞬时存在。
注入装配按请求动态重放(每请求租户点查),注册/编辑/启停即时生效;名称/
描述过既有输入护栏,BLOCK 拒注入(与 bot-store 上架护栏同闸)。
"""

import json
import time
import uuid
from typing import Any

from kongming.connector.models import (
    ConnectorDecl,
    ConnectorExists,
    ConnectorNotFound,
    ConnectorValidationError,
    build_injection_spec,
    resolve_confirmation,
    row_param_decls,
    tool_name,
    validate_declaration,
)
from kongming.connector.ports import (
    ConnectorCaller,
    ConnectorRepo,
    InputGuardrail,
    OperLogger,
    SecretCipher,
    ToolAuditor,
)

# 审计参数摘要截断(与 toolcall.args_digest 同量级的独立实现:本域不传递性
# 依赖 toolcall.models,摘要只做截断不脱敏——测试调用的入参由管理面提交,
# 凭证不入参面,声明材料走独立字段)
_DIGEST_CAP = 500


def _args_digest(args: dict[str, object]) -> str:
    return json.dumps(args, ensure_ascii=False, default=str)[:_DIGEST_CAP]


def decl_secret_fields(decl: ConnectorDecl, secret: str | None) -> dict[str, Any]:
    """声明 → 存储行字段(纯函数):写语义缺省规则落定、白名单/参数 JSONB 化。
    secret=明文材料(由 service 加密后传入 row;none 形态恒 None)。"""
    return {
        "key": decl.key,
        "name": decl.name.strip(),
        "description": decl.description.strip(),
        "method": decl.method,
        "path_template": decl.path_template,
        "params": [
            {
                "name": p.name,
                "type": p.type,
                "required": p.required,
                "description": p.description,
            }
            for p in decl.params
        ],
        "allowed_hosts": list(decl.allowed_hosts),
        "auth_type": decl.auth_type,
        "requires_confirmation": resolve_confirmation(decl.method, decl.requires_confirmation),
        "timeout_ms": decl.timeout_ms,
        "auth_secret_plain": secret,
    }


class ConnectorService:
    def __init__(
        self,
        repo: ConnectorRepo,
        cipher: SecretCipher,
        guardrail: InputGuardrail,
        *,
        timeout_max_ms: int,
        caller: ConnectorCaller | None = None,
        auditor: ToolAuditor | None = None,
        oper: OperLogger | None = None,
    ) -> None:
        self._repo = repo
        self._cipher = cipher
        self._guardrail = guardrail
        self._timeout_max = timeout_max_ms
        self._caller = caller
        self._auditor = auditor
        self._oper = oper

    async def _audit(self, action: str, target: str, detail: dict[str, object]) -> None:
        if self._oper is not None:
            await self._oper.log(action=action, target=target, detail=detail)

    async def _assert_clean_texts(self, *, tenant_id: str, user_id: str, texts: list[str]) -> None:
        """注册闸门(020 护栏,与 bot-store 上架同闸):名称/描述命中 BLOCK
        即拒注册(spec「注入检测面」;fail-closed 在护栏服务内)。"""
        blocked, _ = await self._guardrail.check(
            trace_id=f"connector-{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            tenant_id=tenant_id,
            texts=texts,
        )
        if blocked:
            raise ConnectorValidationError("名称或描述未通过输入护栏审核(疑似注入形态)")

    # ── 注册治理面 ──

    async def create(
        self, *, tenant_id: str, user_id: str, decl: ConnectorDecl, secret: str | None
    ) -> dict[str, Any]:
        """注册(fail-fast):声明校验 → 护栏闸 → 加密落库 → 掩码行返回
        (创建响应亦不回显材料,spec)。"""
        validate_declaration(decl, has_secret=bool(secret), timeout_max_ms=self._timeout_max)
        # 注入检测面含参数描述(评审修复:它是经 params_to_schema 注入给 LLM
        # 的第三个自由文本字段,漏检即成注入面)
        await self._assert_clean_texts(
            tenant_id=tenant_id,
            user_id=user_id,
            texts=[decl.name, decl.description]
            + [p.description for p in decl.params if p.description],
        )
        fields = decl_secret_fields(decl, secret)
        plain = fields.pop("auth_secret_plain")
        row: dict[str, Any] = {
            **fields,
            "tenant_id": tenant_id,
            "created_by": user_id,
            "auth_secret_enc": self._cipher.encrypt(plain) if plain else None,
        }
        try:
            connector_id = await self._repo.create(row)
        except Exception as e:
            if "ux_tool_http_connectors_tenant_key" in str(e):
                raise ConnectorExists(decl.key) from e
            raise
        await self._repo.commit()
        await self._audit("connector.create", decl.key, {"name": decl.name.strip()})
        return await self._must_masked(connector_id, tenant_id)

    async def update(
        self,
        *,
        connector_id: str,
        tenant_id: str,
        user_id: str,
        decl: ConnectorDecl,
        secret: str | None,
    ) -> dict[str, Any]:
        """编辑(凭证空框语义:secret=None=不修改、填入=替换;切换 none 形态
        即清除材料)。校验按「合并后状态」判定——留空时以存量行是否持有材料
        为准(与 bot api-key 空框语义同哲学)。校验/护栏同注册。"""
        current = await self._repo.get_masked(connector_id=connector_id, tenant_id=tenant_id)
        if current is None:
            raise ConnectorNotFound(connector_id)
        merged_has_secret = bool(secret) or (secret is None and bool(current.get("has_secret")))
        validate_declaration(
            decl,
            has_secret=False if decl.auth_type == "none" else merged_has_secret,
            timeout_max_ms=self._timeout_max,
        )
        await self._assert_clean_texts(
            tenant_id=tenant_id,
            user_id=user_id,
            texts=[decl.name, decl.description]
            + [p.description for p in decl.params if p.description],
        )
        fields = decl_secret_fields(decl, None)
        fields.pop("auth_secret_plain")
        if decl.auth_type == "none":
            fields["auth_secret_enc"] = None  # 形态切 none:材料必清
        elif secret:
            fields["auth_secret_enc"] = self._cipher.encrypt(secret)  # 填入=替换
        # secret 为 None 且形态非 none:不带 auth_secret_enc 键=保留现值(留空=不修改)
        if not await self._repo.update(
            connector_id=connector_id, tenant_id=tenant_id, fields=fields
        ):
            raise ConnectorNotFound(connector_id)
        await self._repo.commit()
        # 审计 target=存量行 key(key 不可更新,请求体 key 与行无关;评审修复:
        # 按请求 key 记账会产生指向不存在 key 的审计行)
        await self._audit("connector.update", str(current["key"]), {"name": decl.name.strip()})
        return await self._must_masked(connector_id, tenant_id)

    async def set_enabled(
        self, *, connector_id: str, tenant_id: str, user_id: str, enabled: bool
    ) -> None:
        """启停(注入集按请求装配,即时生效;直调侧经启用态判定同源失效)。"""
        state = "ENABLED" if enabled else "DISABLED"
        if not await self._repo.set_enabled(
            connector_id=connector_id, tenant_id=tenant_id, enabled=state
        ):
            raise ConnectorNotFound(connector_id)
        await self._repo.commit()
        await self._audit(
            "connector.enable" if enabled else "connector.disable",
            connector_id,
            {"enabled": enabled},
        )

    async def delete(self, *, connector_id: str, tenant_id: str, user_id: str) -> None:
        """删除(硬删,identity 域外;落操作审计,spec「删除硬删落审计」)。"""
        row = await self._repo.get_masked(connector_id=connector_id, tenant_id=tenant_id)
        if row is None:
            raise ConnectorNotFound(connector_id)
        await self._repo.delete(connector_id=connector_id, tenant_id=tenant_id)
        await self._repo.commit()
        await self._audit("connector.delete", str(row["key"]), {"name": str(row["name"])})

    # ── 查询面(掩码) ──

    async def _must_masked(self, connector_id: str, tenant_id: str) -> dict[str, Any]:
        row = await self._repo.get_masked(connector_id=connector_id, tenant_id=tenant_id)
        if row is None:
            raise ConnectorNotFound(connector_id)
        return row

    async def get(self, *, connector_id: str, tenant_id: str) -> dict[str, Any]:
        row = await self._repo.get_masked(connector_id=connector_id, tenant_id=tenant_id)
        if row is None:
            raise ConnectorNotFound(connector_id)
        return row

    async def list_connectors(self, *, tenant_id: str) -> list[dict[str, Any]]:
        return await self._repo.list_masked(tenant_id=tenant_id)

    # ── 注入装配(网关主链消费) ──

    async def injection_specs(
        self, *, tenant_id: str, user_id: str, trace_id: str
    ) -> list[dict[str, Any]]:
        """按请求按租户动态装配注入集(spec「注入授权」):启用行 → 护栏
        (BLOCK 拒注入,防注册后护栏规则演进/带外改行)→ 注入声明。
        注册/编辑/启停即时生效,不要求进程重启。"""
        specs: list[dict[str, Any]] = []
        for row in await self._repo.list_enabled(tenant_id=tenant_id):
            name = str(row.get("name") or "")
            description = str(row.get("description") or "")
            # 注入检测面含参数描述(经 params_to_schema 注入给 LLM 的自由文本)
            param_descs = [p.description for p in row_param_decls(row) if p.description]
            blocked, _ = await self._guardrail.check(
                trace_id=trace_id,
                user_id=user_id,
                tenant_id=tenant_id,
                texts=[name, description, *param_descs],
            )
            if blocked:
                continue  # 拒注入(与「连接器从未存在」同形,不暴露原因给 LLM)
            specs.append(build_injection_spec(row))
        return specs

    # ── 测试调用(治理面真实出站一次) ──

    async def test_call(
        self,
        *,
        tenant_id: str,
        user_id: str,
        decl: ConnectorDecl,
        secret: str | None,
        args: dict[str, object],
        use_saved_secret_for: str | None = None,
    ) -> dict[str, Any]:
        """按当前表单声明发起一次真实出站(web-admin「测试调用」):显式
        确认在 UI 层;落工具审计(与执行同表同语义,channel 区分)。
        use_saved_secret_for=已存连接器 key:表单材料留空时用存量凭证
        (编辑态先测后存的空框语义)。"""
        # 存量凭证合并先于声明校验:has_secret 按「合并后是否持有材料」判定
        # (评审修复——先校验后合并时,编辑态空框+use_saved_secret 的先测后存
        # 路径会被材料闸误拒 400,功能死路)
        used_secret = secret
        row_fields = decl_secret_fields(decl, None)
        row_fields.pop("auth_secret_plain")
        if used_secret is None and use_saved_secret_for:
            target = await self._repo.get_call_target(tenant_id=tenant_id, key=use_saved_secret_for)
            if target is not None:
                used_secret = target.get("auth_secret_plain")
                row_fields["auth_type"] = str(target["auth_type"])
        validate_declaration(decl, has_secret=bool(used_secret), timeout_max_ms=self._timeout_max)
        if self._caller is None:
            raise ConnectorValidationError("出站通道未接线(部署缺陷)")
        row: dict[str, Any] = {**row_fields, "tenant_id": tenant_id}
        started = time.monotonic()
        ok, status_code, body_summary, failure = await self._caller.call(
            row=row, secret=used_secret, args=args
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        if self._auditor is not None:
            await self._auditor.record_test_call(
                tool_name=tool_name(decl.key),
                status="EXECUTED" if ok else "ERROR",
                error_reason=failure,
                duration_ms=duration_ms,
                tenant_id=tenant_id,
                user_id=user_id,
                args_digest=_args_digest(args),
            )
        return {
            "ok": ok,
            "status_code": status_code,
            "body_summary": body_summary,
            "failure": failure,
            "duration_ms": duration_ms,
        }


def masked_out_row(row: dict[str, Any]) -> dict[str, Any]:
    """行 → API 输出投影(路由层契约形态;凭证只出掩码,与仓储掩码面同口径)。"""
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "key": str(row["key"]),
        "name": str(row["name"]),
        "description": str(row.get("description") or ""),
        "method": str(row["method"]),
        "path_template": str(row["path_template"]),
        "params": row.get("params") if isinstance(row.get("params"), list) else [],
        "allowed_hosts": row.get("allowed_hosts")
        if isinstance(row.get("allowed_hosts"), list)
        else [],
        "auth_type": str(row["auth_type"]),
        "has_secret": bool(row.get("has_secret")),
        "secret_mask": str(row.get("secret_mask") or ""),
        "requires_confirmation": bool(row.get("requires_confirmation")),
        "timeout_ms": int(row.get("timeout_ms") or 0),
        "enabled": str(row.get("enabled") or "DISABLED"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


__all__ = [
    "ConnectorService",
    "decl_secret_fields",
    "masked_out_row",
]
