"""对话冷启动建议问题生成(039,/internal/suggest-questions 核心逻辑)。

prompt 为 039 全新撰写(非上游资产);遵守全角标点纪律。与 titler/describer
同构:api 侧持 schema 元数据,agent 侧纯脑力(ADR-002)。
降级契约:LLM 失败/输出非法/清洗后为空 → SuggestError,api 侧收敛空清单
(不落缓存,下次访问自然重试),不阻断对话主链路。
安全边界:输入仅为结构摘要(表/列/注释),输出仅为 UI 呈现文本,不构成
任何执行放行依据。
"""

import json
import logging
import re

from pydantic import BaseModel, Field, field_validator

from kongming_agent.llm import LlmClient, LlmError

logger = logging.getLogger(__name__)

# 输出上限(chips 单行可读性优先;api 侧另有防线校验,纵深一层)
_MAX_QUESTIONS = 5
_MAX_QUESTION_LEN = 60
# 摘要截断防超长烧 token(api 侧已封顶 12k,此处兜底再截)
_MAX_DIGEST_CHARS = 12_000

# 结构性字符(换行/制表)破坏单行 chips 形态——清洗为空格折叠
_WS_RE = re.compile(r"\s+")

_SYSTEM_PROMPT = (
    "你是数据问答助手的示例问题策划专家。根据给定的数据库结构摘要，"
    "生成普通业务人员会问的中文示例问题。只输出 JSON，不要输出任何其他文字。"
)

_USER_PROMPT_TEMPLATE = """数据库结构摘要：
{digest}

要求：
1. 生成 3 到 5 个中文自然语言问题，必须是仅凭上述结构就能回答的（可查询出结果）
2. 统计类与明细类都要有（如「共多少」「Top 几」「最近趋势」与「列出清单」）
3. 使用业务口吻的完整问句，每句不超过 30 字，不带编号与引号
4. 只依据摘要中出现的表与列，禁止臆造不存在的业务概念

严格输出如下 JSON（不要输出 markdown 代码块标记）：
{{"questions": ["问题一", "问题二", "问题三"]}}"""  # noqa: E501  # JSON 输出模板单行示例,拆行会改变给 LLM 的格式契约


class SuggestError(Exception):
    """建议生成失败(输入为空/LLM 异常/输出不可解析/清洗后为空)。"""


class SuggestedQuestions(BaseModel):
    """LLM 产出契约(Pydantic 校验,输出前清洗)。"""

    questions: list[str] = Field(default_factory=list)

    @field_validator("questions")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            q = _WS_RE.sub(" ", (item or "").strip())  # 单行折叠
            if not q or len(q) > _MAX_QUESTION_LEN or q in out:
                continue
            out.append(q)
            if len(out) >= _MAX_QUESTIONS:
                break
        return out


def _parse_json_loose(raw: str) -> dict[str, object]:
    """LLM 返回 JSON 的容错解析:剥 markdown 标记、取首尾花括号子串。"""
    text = raw.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError as e:
                raise SuggestError(f"LLM 输出非合法 JSON: {e}") from e
        else:
            raise SuggestError("LLM 输出非 JSON") from None
    if not isinstance(parsed, dict):
        raise SuggestError("LLM 输出非 JSON 对象")
    return parsed


async def suggest_questions(digest: str, llm: LlmClient) -> list[str]:
    """调 LLM 依结构摘要生成建议问句;任何失败抛 SuggestError(api 侧降级)。"""
    d = (digest or "").strip()[:_MAX_DIGEST_CHARS]
    if not d:
        raise SuggestError("结构摘要为空")
    try:
        raw = await llm.chat(
            _SYSTEM_PROMPT,
            _USER_PROMPT_TEMPLATE.format(digest=d),
            temperature=0.3,
            max_tokens=400,
            task="SUGGEST",  # 021:任务路由(缺省主模型;可用注册表定向小模型)
        )
    except LlmError as e:
        raise SuggestError(f"LLM 调用失败: {e}") from e
    try:
        result = SuggestedQuestions.model_validate(_parse_json_loose(raw))
    except Exception as e:
        raise SuggestError(f"LLM 输出不可解析: {e}") from e
    if not result.questions:
        raise SuggestError("LLM 输出清洗后为空,按失败降级")
    logger.info("建议问题生成完成: %d 条", len(result.questions))
    return result.questions
