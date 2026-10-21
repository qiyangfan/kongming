"""bot 用例编排:Bot CRUD + 运行时(权限求交)+ api-key + 审核。零框架。"""

import hashlib
import logging
import secrets
import uuid
from typing import cast

from kongming.bot.models import (
    BOT_KEY_PATTERN,
    InvalidBotKey,
    tools_for_perms,
    validate_tool_subset,
)
from kongming.bot.ports import (
    BotGuardrail,
    BotOperLogger,
    BotRepo,
    DynamicToolLister,
    PermReader,
    ReactRunner,
    UsageSink,
)
from kongming.toolcall.registry import filter_specs

log = logging.getLogger(__name__)

GUARDRAIL_REJECT_TEXT = "内容未通过安全检查,请调整后重试"

# 零工具 Bot 运行闸文案(fail-fast;消费者视角,不暴露内部求交细节)
NO_TOOLS_TEXT = "该助手未配置可用工具,无法执行查询,请联系助手提供方配置后重试"


class BotNotFound(Exception):
    """不存在/非本人/未公开(同形不区分)。"""


class GuardrailRejected(Exception):
    """system_prompt 或输入未过护栏审核。"""


class ApiKeyInvalid(Exception):
    pass


class BotService:
    def __init__(  # noqa: PLR0913
        self,
        repo: BotRepo,
        perms: PermReader,
        runner: ReactRunner,
        guardrail: BotGuardrail,
        oper_logger: BotOperLogger,
        dynamic_tools: DynamicToolLister | None = None,  # MCP 目录(未注入=纯静态)
        usage_sink: UsageSink | None = None,  # bot 用量落库(未注入=不落)
    ) -> None:
        self._repo = repo
        self._perms = perms
        self._runner = runner
        self._guardrail = guardrail
        self._oper = oper_logger
        self._dynamic_tools = dynamic_tools
        self._usage_sink = usage_sink

    async def _sink_usage(self, reply: dict[str, object], *, tenant_id: str, bot_id: str) -> None:
        """请求内同步落用量(计费基准);失败仅日志不阻断应答。"""
        if self._usage_sink is None:
            return
        records_raw = reply.get("usage_records")
        if not isinstance(records_raw, list) or not records_raw:
            return
        records = [
            dict(cast("dict[str, object]", r))
            for r in cast("list[object]", records_raw)
            if isinstance(r, dict)
        ]
        if not records:
            return
        try:
            await self._usage_sink.insert_bot_usage(records, tenant_id=tenant_id, bot_id=bot_id)
        except Exception:
            log.warning(
                "bot 用量落库失败(不阻断应答): tenant=%s bot=%s 条数=%d",
                tenant_id,
                bot_id,
                len(records),
                exc_info=True,
            )

    async def _mcp_names(self, tenant_id: str) -> list[str]:
        """租户已注册 MCP 工具全名(未接线/空 → 纯静态口径)。"""
        if self._dynamic_tools is None:
            return []
        return [
            str(e.get("qualified") or "")
            for e in await self._dynamic_tools.list_dynamic(tenant_id=tenant_id)
        ]

    @staticmethod
    def _registered_names(mcp_names: list[str]) -> set[str]:
        from kongming.toolcall.registry import REGISTRY

        return set(REGISTRY.keys()) | set(mcp_names)

    # ── CRUD ──

    async def create(self, *, user_id: str, tenant_id: str, payload: dict[str, object]) -> str:
        bot_key = str(payload["bot_key"])
        if not BOT_KEY_PATTERN.fullmatch(bot_key):
            raise InvalidBotKey(bot_key)
        raw_tools: object = payload.get("tools") or []
        tools = (
            [str(t) for t in cast("list[object]", raw_tools)] if isinstance(raw_tools, list) else []
        )
        mcp_names = await self._mcp_names(tenant_id)
        allowed = tools_for_perms(await self._perms.list_codes(user_id), mcp_names)
        validate_tool_subset(tools, allowed, registered_names=self._registered_names(mcp_names))
        # datasource 归属校验(spec 字面立约;default 恒可用)
        ds_key = str(payload.get("datasource_key") or "default")
        if ds_key != "default":
            await self._assert_datasource(tenant_id, ds_key)
        # model 注册表校验(非空时;实时查 agent 注册表,不可达 fail-closed 拒)
        model = payload.get("model")
        if model is not None:
            available = await self._runner.list_models()
            if str(model) not in available:
                raise InvalidBotKey(f"模型未注册: {model}")
        # 发布治理:system_prompt 过护栏审核(spec)
        await self.validate_prompt(
            user_id=user_id, tenant_id=tenant_id, prompt=str(payload["system_prompt"])
        )
        row_id = await self._repo.create_bot(
            tenant_id=tenant_id,
            bot_key=bot_key,
            name=str(payload["name"]),
            system_prompt=str(payload["system_prompt"]),
            tools=tools,
            datasource_key=str(payload.get("datasource_key") or "default"),
            model=payload.get("model"),
            is_public=bool(payload.get("is_public", False)),
            created_by=user_id,
        )
        await self._oper.add(user_id, "bot.create", bot_key, {"datasource_key": ds_key})
        await self._repo.commit()
        return row_id

    async def _assert_datasource(self, tenant_id: str, datasource_key: str) -> None:
        """非 default 源存在+启用+归属(同形 InvalidBotKey 400 防探测)。"""
        row = await self._repo.find_datasource(datasource_key=datasource_key)
        if row is None or row["status"] != "ACTIVE" or str(row["tenant_id"]) != tenant_id:
            raise InvalidBotKey(f"数据源不可用: {datasource_key}")

    async def resolve_tools(
        self, *, creator_id: str, tenant_id: str, row: dict[str, object]
    ) -> list[dict[str, object]]:
        """实际可用工具集(单一真理源,运行闸/上架闸同源):
        Bot 子集 ∩ 创建者当前权限 → 剔除需确认写工具→ MCP 只读补入。
        返回空集 = 该 Bot 在当前状态下不存在任何可执行工具。"""
        # 权限求交(spec:Bot 子集 ∩ 创建者当前权限);
        # filter_specs 接收权限点,传入工具名会导致过滤结果为空
        creator_perms = await self._perms.list_codes(creator_id)
        mcp_names = await self._mcp_names(tenant_id)
        allowed_names = tools_for_perms(creator_perms, mcp_names)
        raw = row.get("tools")
        raw_bot_tools = cast("list[object]", raw) if isinstance(raw, list) else []
        bot_tools = [str(t) for t in raw_bot_tools if str(t) in allowed_names]
        tools = [dict(s) for s in filter_specs(creator_perms) if s["name"] in set(bot_tools)]
        # Bot 渠道无确认交互(公开页/OpenAI 无会话)——剔除需确认的写工具,
        # 防确认死信+LLM 谎称卡片已弹出
        from kongming.toolcall.registry import REGISTRY as _TOOL_REG

        tools = [t for t in tools if not _TOOL_REG[str(t["name"])].require_confirm]
        # MCP 工具补入(filter_specs 只覆盖静态集);写工具同
        # 剔除(无 readOnlyHint=require_confirm,Bot 渠道确认死信)
        mcp_wanted = {t for t in bot_tools if t.startswith("mcp__")}
        if mcp_wanted and self._dynamic_tools is not None:
            for entry in await self._dynamic_tools.list_dynamic(tenant_id=tenant_id):
                q = str(entry.get("qualified") or "")
                if q not in mcp_wanted or not bool(entry.get("read_only")):
                    continue
                schema = entry.get("schema")
                tools.append(
                    {
                        "name": q,
                        "description": str(entry.get("description") or ""),
                        "parameters": schema if isinstance(schema, dict) else {"type": "object"},
                        "write": False,
                        "require_confirm": False,
                    }
                )
        return tools

    async def run(
        self,
        *,
        bot_key: str,
        question: str,
        channel: str,  # web_embed / api / mcp/ store
        tenant_id_hint: str | None = None,  # api-key 渠道从 key 行带出
        definition_override: dict[str, object] | None = None,  # 商店直跑消费快照
    ) -> dict[str, object]:
        """Bot 运行时:装载(启用/公开闸)→ 权限求交 → ReAct → 护栏 → 响应。

        返回 {success, summary, sql, columns, rows, stats, retry_count, error}。
        definition_override(商店渠道传当前上架版本快照)覆写系统提示词
        与工具集——货架消费快照而非活定义;公开页/api-key 等既有渠道不传,
        消费活定义为既有契约。
        """
        tenant_id = tenant_id_hint
        if tenant_id is None:
            # web 嵌入:bot_key 全局定位(先查全表拿租户——bot_key 租户内唯一,
            # 公开页不带租户头;经 key 定位租户是刻意的)
            row = await self._repo.get_by_key_anytenant(bot_key)
        else:
            row = await self._repo.get_by_key(tenant_id=tenant_id, bot_key=bot_key)
        if row is None or not row["enabled"]:
            raise BotNotFound(bot_key)
        if channel == "web_embed" and not row["is_public"]:
            raise BotNotFound(bot_key)
        creator = str(row["created_by"])
        # 创建者禁用即不可运行(权限继承的账号态闸)
        creator_status = await self._repo.find_user_status(creator)
        if creator_status != "ACTIVE":
            raise BotNotFound(bot_key)  # 创建者不可用同形 404(防探测)
        trace_id = uuid.uuid4().hex
        tenant_id = str(row["tenant_id"])
        # 输入护栏(与 chat 同口径)
        blocked, processed = await self._guardrail.check_input(
            trace_id=trace_id, user_id=creator, tenant_id=tenant_id, texts=[question]
        )
        if blocked:
            return {"success": False, "error": GUARDRAIL_REJECT_TEXT, "summary": ""}
        # 权限求交与工具裁剪(run/上架闸同源,见 resolve_tools);
        # 商店渠道在快照定义上求交(零工具运行闸口径单源不变)
        run_row: dict[str, object] = dict(row)
        if definition_override is not None:
            sp = definition_override.get("system_prompt")
            if isinstance(sp, str) and sp:
                run_row["system_prompt"] = sp
            tools_override = definition_override.get("tools")
            if isinstance(tools_override, list):
                run_row["tools"] = [str(t) for t in cast("list[object]", tools_override)]
        tools = await self.resolve_tools(creator_id=creator, tenant_id=tenant_id, row=run_row)
        if not tools:
            # 零工具运行闸:无工具进 ReAct 只会产出「文本伪调用」假应答
            # (内核纪律段教模型调工具而模型无工具可调)——必败运行不花
            # LLM 调用,同构失败形态直返(与 AgentUnavailable 分支同构)
            return {
                "success": False,
                "error": NO_TOOLS_TEXT,
                "summary": "",
                "sql": "",
                "columns": [],
                "rows": [],
                "stats": {},
                "retry_count": 0,
            }
        from kongming.chat.agent_client import AgentUnavailable

        try:
            reply = await self._runner.run_react(
                trace_id=trace_id,
                tenant_key=str(row["tenant_key"]),
                question=processed[0],
                user_id=creator,
                datasource_key=str(row["datasource_key"]),
                tools=tools,
                bot_system_prompt=str(run_row["system_prompt"]),  # 商店渠道=快照
                model=None if row["model"] is None else str(row["model"]),
                channel=channel,
            )
        except AgentUnavailable:
            # agent 故障收敛友好错误(不裸 500)
            return {
                "success": False,
                "error": "助手暂不可用,请稍后重试",
                "summary": "",
                "sql": "",
                "columns": [],
                "rows": [],
                "stats": {},
                "retry_count": 0,
            }
        await self._sink_usage(reply, tenant_id=tenant_id, bot_id=str(row["id"]))
        summary = str(reply.get("summary") or "")
        error_text = str(reply.get("error") or "")
        # error 一并过输出护栏(FAILED 错误原文可能含凭证形态,
        # OpenAI 兼容端点 content 回退链会消费它——与 chat 域 _finalize_reply
        # 同纪律)
        filtered = await self._guardrail.filter_output(
            trace_id=trace_id,
            user_id=creator,
            tenant_id=tenant_id,
            texts=[summary, error_text],
        )
        return {
            "success": bool(reply.get("success")),
            "summary": filtered[0],
            "sql": str(reply.get("sql") or ""),
            "columns": reply.get("columns") or [],
            "rows": reply.get("rows") or [],
            "stats": reply.get("stats") or {},
            "retry_count": (
                int(str(reply.get("retry_count"))) if reply.get("retry_count") is not None else 0
            ),
            "error": filtered[1] or None,
        }

    async def validate_prompt(self, *, user_id: str, tenant_id: str, prompt: str) -> None:
        """system_prompt 护栏审核(创建/编辑共用;命中抛 GuardrailRejected)。"""
        blocked, _ = await self._guardrail.check_input(
            trace_id=f"bot-{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            tenant_id=tenant_id,
            texts=[prompt],
        )
        if blocked:
            raise GuardrailRejected

    # ── api-key ──

    async def create_key(
        self, *, bot_id: str, tenant_id: str, user_id: str, name: str
    ) -> tuple[str, str]:
        """创建 API key 并返回明文和 key_id；明文只在创建响应中返回。"""
        plain = "km_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(plain.encode()).hexdigest()
        key_id = await self._repo.insert_api_key(
            tenant_id=tenant_id,
            bot_id=bot_id,
            key_hash=key_hash,
            name=name,
            scopes={"bot_keys": []},
        )
        await self._repo.commit()
        return plain, key_id

    async def resolve_api_key(self, plain: str) -> dict[str, object]:
        """X-Api-Key 鉴权:sha256 查行+未撤销+Bot 启用;返回 {bot_key, tenant_id}。"""
        key_hash = hashlib.sha256(plain.encode()).hexdigest()
        row = await self._repo.find_api_key(key_hash=key_hash)
        if row is None or not row["bot_enabled"]:
            raise ApiKeyInvalid
        await self._repo.touch_key_used(key_id=str(row["id"]))
        await self._repo.commit()
        # key 绑定单 Bot,scopes 集合校验语义冗余,不再校验;
        # bot_id 供 BOT 级配额判定与用量归属(消费方既有键不变)
        return {
            "bot_key": str(row["bot_key"]),
            "tenant_id": str(row["tenant_id"]),
            "bot_id": str(row["bot_id"]),
        }
