"""MCP 入站端点(028):POST /mcp —— Bot as MCP tools(design D5)。

api-key 鉴权:X-Api-Key(025 同款)或 Authorization: Bearer(MCP 客户端
惯例);key 绑定单 Bot(025 R1-M5),tools/list 恰返回该 Bot。
方法子集:initialize/ping/tools/list/tools/call;notifications/* → 202 空;
未知方法 → -32601。无会话态(不协商 Mcp-Session-Id)。
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from kongming.bot.router import _get_service  # 025 组合根复用(装配层)
from kongming.bot.service import ApiKeyInvalid, BotNotFound, BotService

log = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-inbound"])

_PROTOCOL_VERSION = "2025-03-26"
_BODY_MAX_BYTES = 1 * 1024 * 1024  # 与 trigger webhook 同值(免登录端点统一闸)


def _result(rpc_id: Any, payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": payload})


def _error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    svc: Annotated[BotService, Depends(_get_service)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Response:
    # 入站 body 闸(未认证先拒):Content-Length 声明超限不读 body;
    # 实测超限拒——闸前置于 notifications 短路/鉴权/解析(trigger 同范式)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _BODY_MAX_BYTES:
        return JSONResponse(status_code=413, content={"detail": "PAYLOAD_TOO_LARGE"})
    raw = await request.body()
    if len(raw) > _BODY_MAX_BYTES:
        return JSONResponse(status_code=413, content={"detail": "PAYLOAD_TOO_LARGE"})
    try:
        request_body: dict[str, Any] = json.loads(raw)
        if not isinstance(request_body, dict):
            raise ValueError("not an object")
    except (ValueError, UnicodeDecodeError):
        return _error(None, -32700, "Parse error")
    # notifications/*(MCP 生命周期通告):无 id,202 空响应
    rpc_id = request_body.get("id")
    method = str(request_body.get("method") or "")
    if rpc_id is None:
        return Response(status_code=202)

    key = _extract_key(authorization, x_api_key)
    if not key:
        return JSONResponse(status_code=401, content={"detail": "API_KEY_REQUIRED"})
    try:
        key_info = await svc.resolve_api_key(key)
    except ApiKeyInvalid:
        # 无效/撤销/绑定 Bot 停用同形 401(不暴露任何工具清单)
        return JSONResponse(status_code=401, content={"detail": "API_KEY_INVALID"})
    bot_key = str(key_info["bot_key"])

    if method in ("initialize", "ping"):
        return _result(
            rpc_id,
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "kongming", "version": "1.0"},
            },
        )
    if method == "tools/list":
        return _result(
            rpc_id,
            {
                "tools": [
                    {
                        "name": f"bot__{bot_key}",
                        "description": f"Kongming Bot: {bot_key}",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "自然语言问题",
                                }
                            },
                            "required": ["question"],
                        },
                    }
                ]
            },
        )
    if method == "tools/call":
        params = request_body.get("params")
        if not isinstance(params, dict):
            return _error(rpc_id, -32602, "缺 params")
        name = str(params.get("name") or "")
        if name != f"bot__{bot_key}":
            # 其他 key/租户的 Bot 不可见不可调用(同形未知工具)
            return _error(rpc_id, -32602, "未知工具")
        args = params.get("arguments")
        question = str(args.get("question") or "") if isinstance(args, dict) else ""
        if not question.strip():
            return _error(rpc_id, -32602, "question 为空")
        if len(question) > 2000:
            # R1-A3:与 web 端 BotChatIn max_length 同闸(创建者配额保护)
            return _error(rpc_id, -32602, "question 超 2000 字符上限")
        try:
            out = await svc.run(
                bot_key=bot_key,
                question=question,
                channel="mcp",  # 028 渠道溯源
                tenant_id_hint=str(key_info["tenant_id"]),
            )
        except BotNotFound:
            return _error(rpc_id, -32602, "未知工具")
        except Exception:
            log.warning("mcp tools/call 执行异常", exc_info=True)
            return _result(
                rpc_id,
                {
                    "content": [{"type": "text", "text": "执行失败,请稍后再试"}],
                    "isError": True,
                },
            )
        # R1-A7:失败轮(护栏拦截/助手不可用)透 error 文本+isError——
        # 否则客户端见「成功+空文本」无法排障;error 本是用户向友好文案
        if not out.get("success"):
            return _result(
                rpc_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": str(out.get("error") or "执行失败"),
                        }
                    ],
                    "isError": True,
                },
            )
        text = str(out.get("summary") or "")
        return _result(
            rpc_id, {"content": [{"type": "text", "text": text}], "isError": False}
        )
    return _error(rpc_id, -32601, f"方法不存在: {method[:60]}")
