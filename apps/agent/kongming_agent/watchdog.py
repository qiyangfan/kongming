"""任务停滞判读(看门狗 judge 侧，单次判读契约)。

api 巡检循环对心跳超阈的在途任务发起**单次**判读:输入任务目标、最近
事件尾与滞留秒数，输出停滞/仍进展 + 一行理由。同一滞留段只判读一次由
api 侧事件标记去重保证，本模块不做重试与缓存。
降级契约:LLM 失败/输出不可解析 → JudgeError,api 侧按判读不可用处理
(落标记事件，二次超窗机械收口)。
prompt 为全新撰写，不适用 prompts.py 逐字移植纪律。
"""

import json
import logging

from kongming_agent.graph.nodes import parse_summary_json
from kongming_agent.llm import LlmClient, LlmError

logger = logging.getLogger(__name__)

_MAX_GOAL_LEN = 4_000
_MAX_TAIL = 8  # 与 api 侧查询截断同值(防御双闸)

_SYSTEM_PROMPT = (
    "你是长时任务运行监督员。根据任务目标与最近事件时间线，判断任务是已停滞"
    "(卡死/原地打转/无实质进展)还是仍在正常推进。只输出严格 JSON，不要解释，"
    "不要 markdown 标记。"
)

_USER_PROMPT_TEMPLATE = """判断下列任务当前是否停滞，输出严格 JSON。

判定口径:
1. 停滞=事件时间线长时间无新进展、或反复同类动作无产出、或明显偏离目标空转;
2. 仍进展=步骤/工具调用在按序推进，即便单步耗时较长;
3. 单步长耗时本身不构成停滞，除非已超出该步骤的合理时长;
4. reason 为一句话(≤80 字)说明判定依据。

任务目标:
{goal}

距最后一次活动已滞留秒数:{stale_sec}

最近事件时间线(时间升序，晚在后):
{events}

输出形如:{{"verdict":"stagnant","reason":"…"}}
verdict 只能取 stagnant 或 progressing。"""


class JudgeError(Exception):
    """判读失败(LLM 异常/输出不可解析)——api 侧按判读不可用兜底。"""


async def judge_stagnation(
    *,
    goal: str,
    events_tail: list[dict[str, object]],
    stale_sec: int,
    llm: LlmClient,
) -> dict[str, object]:
    """判读任务是否停滞;任何失败抛 JudgeError(api 侧降级机械兜底)。"""
    g = goal.strip()[:_MAX_GOAL_LEN]
    if not g:
        raise JudgeError("空任务目标")
    tail = [
        {"at": str(e.get("at") or ""), "kind": str(e.get("kind") or "")}
        for e in events_tail[-_MAX_TAIL:]
    ]
    try:
        raw = await llm.chat(
            _SYSTEM_PROMPT,
            _USER_PROMPT_TEMPLATE.format(
                goal=g,
                stale_sec=stale_sec,
                events=json.dumps(tail, ensure_ascii=False),
            ),
            temperature=0.0,
            max_tokens=200,
            task="WATCHDOG",  # 任务路由(部署可指到性价比档;未路由回退主模型)
        )
    except LlmError as e:
        raise JudgeError(f"LLM 调用失败: {e}") from e
    parsed = parse_summary_json(raw)
    if not parsed:
        raise JudgeError("判读输出不可解析")
    verdict = str(parsed.get("verdict") or "")
    reason = str(parsed.get("reason") or "").strip()[:200]
    if verdict not in ("stagnant", "progressing") or not reason:
        raise JudgeError("判读输出缺失必要字段")
    logger.info("任务停滞判读完成")
    return {"verdict": verdict, "reason": reason}
