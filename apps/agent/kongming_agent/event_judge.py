"""事件立项判读(触发器 task 目标过滤闸的语义层，单次判读契约)。

api 过滤闸对通过程序层条件的候选事件发起**单次**判读:输入事件来源、
标准化摘要与立项预览(模板目标前缀),输出立项/否决 + 一行理由。判读
材料是适配器白名单摘要(不含凭据与原始载荷),判定口径=「该事件是否
值得按预览中的模板立项」。降级契约:LLM 失败/输出不可解析 → JudgeError,
api 侧按判读不可用 SKIPPED(不默认立项)。
prompt 为全新撰写，不适用 prompts.py 逐字移植纪律。
"""

import json
import logging

from kongming_agent.graph.nodes import parse_summary_json
from kongming_agent.llm import LlmClient, LlmError

logger = logging.getLogger(__name__)

_MAX_SUMMARY_LEN = 2_000
_MAX_PREVIEW_LEN = 400

_SYSTEM_PROMPT = (
    "你是自动化运维平台的事件值守员。根据事件摘要与立项预览，判断该事件"
    "是否值得创建一个数据任务去处理，还是应当忽略(噪音/重复/无处理价值/"
    "明显不属于平台能力范围)。只输出严格 JSON,不要解释，不要 markdown 标记。"
)

_USER_PROMPT_TEMPLATE = """判断下列事件是否值得立项处理，输出严格 JSON。

判定口径:
1. create=事件描述了值得数据分析处理的实际问题、请求或异常;
2. skip=闲聊寒暄、无实质内容、与立项预览能力明显无关、或纯重复通知;
3. 拿不准时倾向 skip(宁可漏建，不可噪音灌库);
4. reason 为一句话(≤60 字)说明判定依据。

事件来源:{source}

事件摘要:
{summary}

立项预览(建出来的任务将按此目标处理该事件):
{goal_preview}

输出形如:{{"verdict":"create","reason":"…"}}
verdict 只能取 create 或 skip。"""


class JudgeError(Exception):
    """判读失败(LLM 异常/输出不可解析)——api 侧按判读不可用 SKIPPED。"""


async def judge_event(
    *,
    source: str,
    summary: str,
    goal_preview: str,
    llm: LlmClient,
) -> dict[str, object]:
    """判读事件是否值得立项;任何失败抛 JudgeError(api 侧降级 SKIPPED)。"""
    s = summary.strip()[:_MAX_SUMMARY_LEN]
    p = goal_preview.strip()[:_MAX_PREVIEW_LEN]
    if not s:
        raise JudgeError("空事件摘要")
    try:
        raw = await llm.chat(
            _SYSTEM_PROMPT,
            _USER_PROMPT_TEMPLATE.format(
                source=source,
                summary=s,
                goal_preview=json.dumps(p, ensure_ascii=False),
            ),
            temperature=0.0,
            max_tokens=150,
            task="WATCHDOG",  # 同族单次判读共用性价比档路由
        )
    except LlmError as e:
        raise JudgeError(f"LLM 调用失败: {e}") from e
    parsed = parse_summary_json(raw)
    if not parsed:
        raise JudgeError("判读输出不可解析")
    verdict = str(parsed.get("verdict") or "")
    reason = str(parsed.get("reason") or "").strip()[:160]
    if verdict not in ("create", "skip") or not reason:
        raise JudgeError("判读输出缺失必要字段")
    logger.info("事件立项判读完成")
    return {"verdict": verdict, "reason": reason}
