"""成功问答轮的异步评分任务(spec「答案异步评分」)。

形态同 title/memory 后台任务:独立短会话分段(守卫读→LLM 调用池外等待
→条件写),任何异常只记日志不外抛。评分完全图外:主链路响应与耗时
零感知;评审降级/证据不可装配/已有评分各短路,均静默。
"""

import json
import logging

from sqlalchemy import text

from kongming.chat.agent_client import AgentClient
from kongming.chat.scoring import (
    assemble_data_evidence,
    assemble_doc_evidence,
    build_material,
    compute_score,
    decomposition_payload,
    parse_decomposition,
)
from kongming.chat.scoring_repo import SqlScoreRepo
from kongming.db import get_sessionmaker

log = logging.getLogger(__name__)


async def score_answer_task(*, message_id: str, agent: AgentClient) -> None:
    """评分单条 assistant 消息;全部异常收敛(后台任务语义)。"""
    try:
        material, trace_id, tenant_key = await _assemble_material(message_id)
        if material is None:
            return  # 非目标轮/证据不可装配(静默,守卫口径见 _assemble_material)
        result = await agent.score_answer(
            material=material, trace_id=trace_id, tenant_key=tenant_key
        )
        if result is None:
            return  # 评审降级(不落分仅日志,agent 侧已记 warning)
        raw_decomposition = result.get("decomposition")
        if not isinstance(raw_decomposition, dict):
            return
        d = parse_decomposition(raw_decomposition)
        if d is None:
            return
        ts = compute_score(d)
        async with get_sessionmaker()() as s:
            inserted = await SqlScoreRepo(s).insert_if_absent(
                message_id=message_id,
                score=ts.score,
                grounding=ts.grounding,
                coverage=ts.coverage,
                decomposition=decomposition_payload(d),
                score_model=str(result.get("score_model") or ""),
            )
            await s.commit()
            if not inserted:
                log.info("评分幂等跳过 message=%s", message_id)
    except Exception:  # noqa: BLE001  # 后台任务永不外抛
        log.warning("评分任务异常 message=%s", message_id, exc_info=True)


async def _assemble_material(message_id: str) -> tuple[str | None, str, str]:
    """守卫读 + 证据装配(短会话,块退出即释放连接)。

    返回 (材料, trace_id, tenant_key);材料 None = 不评分——role/status
    不符(非 SUCCESS assistant 轮)、结果 JSON 解析失败、证据不可装配
    (无结果集且无有效来源引用)各归此口径。tenant_key=租户键(用量归因
    维度,api 侧按 key 反解;与问答链路同口径,SHALL NOT 传租户 UUID)。"""
    from kongming.rag.documents_repo import SqlDocumentRepo

    async with get_sessionmaker()() as s:
        row = (
            (
                await s.execute(
                    text(
                        "SELECT m.role, m.status, m.question, m.resolved_question,"
                        "       m.result_json, m.summary, m.trace_id,"
                        "       se.tenant_id AS tenant_id, te.tenant_key AS tenant_key"
                        " FROM public.chat_message m"
                        " JOIN public.chat_session se ON se.id = m.session_id"
                        " LEFT JOIN public.sys_tenant te ON te.id = se.tenant_id"
                        " WHERE m.id = :mid"
                    ),
                    {"mid": message_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["role"] != "ASSISTANT" or row["status"] != "SUCCESS":
            return None, "", ""
        try:
            payload = json.loads(str(row["result_json"] or "{}"))
        except ValueError:
            log.warning("评分证据装配失败(result_json 非法) message=%s", message_id)
            return None, "", ""
        if not isinstance(payload, dict):
            return None, "", ""
        columns = payload.get("columns")
        rows = payload.get("rows")
        stats = payload.get("stats")
        sources = payload.get("sources")
        col_list = [str(c) for c in columns] if isinstance(columns, list) else []
        row_list = [list(r) for r in rows] if isinstance(rows, list) else []
        stat_map = dict(stats) if isinstance(stats, dict) else {}
        tenant_id = str(row["tenant_id"] or "")
        tenant_key = str(row["tenant_key"] or "")
        trace_id = str(row["trace_id"] or "")
        answer = str(row["summary"] or "")
        if not answer.strip():
            return None, "", ""  # 无答案文本(如再执行轮)不评分
        if col_list and row_list:
            evidence = assemble_data_evidence(col_list, row_list, stat_map)
        elif isinstance(sources, list) and sources and tenant_id:
            typed_sources: list[dict[str, str]] = [
                {k: str(v) for k, v in src.items() if isinstance(v, str)}
                for src in sources
                if isinstance(src, dict)
            ]
            refs = [src["source_ref"] for src in typed_sources if src.get("source_ref")]
            contents = (
                await SqlDocumentRepo(s).fetch_contents_by_refs(tenant_id=tenant_id, refs=refs)
                if refs
                else {}
            )
            evidence = assemble_doc_evidence(typed_sources, contents)
        else:
            return None, "", ""  # 证据不可装配(spec:该轮不评分)
        if not evidence.strip():
            return None, "", ""
        material = build_material(
            question=str(row["question"] or ""),
            resolved_question=str(row["resolved_question"] or "") or None,
            answer=answer,
            evidence=evidence,
        )
    return material, trace_id, tenant_key
