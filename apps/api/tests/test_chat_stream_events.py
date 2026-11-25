"""公开流事件信封(query-streaming「公开事件字段与接口说明」)。

每条业务帧 data: {"type":"…","data":{…}};type 决定 data 的结构。
严格性:类型与数据错配、额外字段、字符串重试次数、空结果一律拒绝;
error 的 reason_code 必填且只允许两种流错误取值。
"""

from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from kongming.chat.schemas import ChartOut, ChatQueryOut, ChatStreamEvent

_adapter: TypeAdapter[ChatStreamEvent] = TypeAdapter(ChatStreamEvent)


def _ok(frame: dict[str, object]) -> None:
    _adapter.validate_python(frame)


def _reject(frame: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _adapter.validate_python(frame)


def test_each_legal_event_type() -> None:
    _ok({"type": "stage", "data": {"node": "generate", "status": "running", "retry_count": 0}})
    _ok({"type": "sql", "data": {"sql": "select 1"}})
    _ok({"type": "summary", "data": {"delta": "答案"}})
    _ok({"type": "tool_call", "data": {"tool_name": "sql_query", "status": "running"}})
    _ok(
        {
            "type": "result",
            "data": _result_data(),
        }
    )
    _ok(
        {
            "type": "error",
            "data": {"error": "服务暂时不可用", "reason_code": "AGENT_UNAVAILABLE"},
        }
    )


def _result_data() -> dict[str, object]:
    """合法 result.data:与同步响应同构(ChatQueryOut 全字段)。"""
    out = ChatQueryOut(
        session_id="01936d7e-8c1b-7cc2-9d4e-5fa1b2c3d4e9",
        success=True,
        verdict="PASS",
        summary="共 3 行",
        insights=[],
        stats={},
        columns=["c"],
        rows=[["1"]],
        sql="select 1",
        chart=ChartOut(type="table"),
        retry_count=0,
        error=None,
    )
    return out.model_dump(mode="json")


def test_type_data_mismatch_rejected() -> None:
    # stage 的 data 含 sql、retry_count 是字符串
    _reject({"type": "stage", "data": {"sql": "select 1"}})
    _reject(
        {"type": "stage", "data": {"node": "generate", "status": "running", "retry_count": "1"}}
    )
    # sql 的 data 是 summary 形状
    _reject({"type": "sql", "data": {"delta": "答案"}})


def test_extra_fields_rejected_on_envelope_and_process_data() -> None:
    _reject({"type": "sql", "data": {"sql": "select 1"}, "trace": "诊断内容"})
    _reject({"type": "sql", "data": {"sql": "select 1", "extra": 1}})


def test_error_reason_code_enum_and_required() -> None:
    # reason_code 缺失或非枚举值都拒绝
    _reject({"type": "error", "data": {"error": "失败"}})
    _reject({"type": "error", "data": {"error": "失败", "reason_code": "SOMETHING_ELSE"}})


def test_empty_result_rejected() -> None:
    _reject({"type": "result", "data": {}})


def test_unknown_type_rejected() -> None:
    _reject({"type": "trace", "data": {"node": "n", "fields": {}}})
    _reject({"type": "whatever", "data": {}})


# ============ 接口说明(200 只声明事件流;x-sse-data-schema 六分支) ============


def _exported_contract() -> dict[str, Any]:
    """导出产物(含 SSE content 后处理)是契约的唯一事实源。"""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_200_declares_event_stream_only() -> None:
    r200 = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", _exported_contract()["paths"]["/chat/query/stream"]["post"])[
            "responses"
        ]["200"],
    )
    assert list(cast("dict[str, Any]", r200["content"]).keys()) == ["text/event-stream"]


def test_sse_data_schema_has_six_discriminated_branches() -> None:
    contract = _exported_contract()
    op = cast("dict[str, Any]", contract["paths"]["/chat/query/stream"]["post"])
    sse = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", op["responses"]["200"])["content"],
    )["text/event-stream"]
    data_schema = cast("dict[str, Any]", sse["x-sse-data-schema"])
    assert len(cast("list[object]", data_schema["oneOf"])) == 6
    mapping = cast("dict[str, str]", data_schema["discriminator"]["mapping"])
    assert set(mapping) == {"stage", "sql", "summary", "tool_call", "result", "error"}


def test_exported_component_schemas_require_type_and_data() -> None:
    comps = cast("dict[str, Any]", _exported_contract()["components"]["schemas"])
    for name in (
        "StreamStageEvent",
        "StreamSqlEvent",
        "StreamSummaryEvent",
        "StreamToolCallEvent",
        "StreamResultEvent",
        "StreamErrorEvent",
    ):
        assert set(cast("list[str]", comps[name]["required"])) == {"type", "data"}
