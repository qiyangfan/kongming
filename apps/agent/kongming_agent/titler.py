"""会话标题 LLM 改写(010,/internal/rewrite-title 核心逻辑)。

prompt 为 010 全新撰写——上游无 LLM 标题资产(上游标题=首问截断 30 字符),
本文件非 prompts.py 逐字移植资产,不适用全角标点纪律。
降级契约:LLM 失败/输出非法 → TitleError,api 侧保持截断标题(不阻断问答)。
"""

import logging

from kongming_agent.llm import LlmClient, LlmError

logger = logging.getLogger(__name__)

_MAX_TITLE_LEN = 50  # LLM 输出防御上限(api 侧列上限 200,标题语义宜短)
_MAX_QUESTION_LEN = 500  # 问句截断:标题语义在开头,防超长问句烧 token

_SYSTEM_PROMPT = (
    "你是会话标题撰写专家。根据用户的提问,生成一个简洁的会话标题。"
    "只输出标题本身,不要输出任何其他文字。"
)

_USER_PROMPT_TEMPLATE = """请为以下用户提问撰写会话标题。

要求:
1. 不超过 20 个字,概括提问的核心主题(名词短语,非完整句子)
2. 不使用标点符号结尾,不使用引号/书名号包裹
3. 与用户提问使用同一语言

用户提问:
{question}"""

# LLM 常见包裹符(strip 双侧任意组合)
_WRAPPERS = "\"'`「」『』《》“”‘’"
# LLM 常见标签前缀(「标题:xxx」形态)
_LABEL_PREFIXES = ("标题:", "标题：", "title:", "Title:")


class TitleError(Exception):
    """标题改写失败(空问句/LLM 异常/输出净化后为空)——api 侧保持截断标题。"""


def sanitize_title(raw: str) -> str:
    """净化 LLM 输出:空白折叠(换行→单空格)、去包裹引号与标签前缀、截断上限。"""
    t = " ".join(raw.split())
    for prefix in _LABEL_PREFIXES:
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    t = t.strip(_WRAPPERS).strip()
    return t[:_MAX_TITLE_LEN]


async def rewrite_title(question: str, llm: LlmClient) -> str:
    """调 LLM 将会话首问改写为简洁标题;任何失败抛 TitleError(api 侧降级)。"""
    q = question.strip()[:_MAX_QUESTION_LEN]
    if not q:
        raise TitleError("空问句")
    try:
        raw = await llm.chat(
            _SYSTEM_PROMPT,
            _USER_PROMPT_TEMPLATE.format(question=q),
            temperature=0.3,
            max_tokens=60,  # 标题 ≤20 字,封顶防长跑输出烧 token(design D1)
        )
    except LlmError as e:
        raise TitleError(f"LLM 调用失败: {e}") from e
    title = sanitize_title(raw)
    if not title:
        raise TitleError("LLM 输出净化后为空")
    logger.info("会话标题改写完成: %s", title)
    return title
