"""答案评审(在线逐轮异步评分的 judge 侧)。

职责分工(spec「答案异步评分(分解与确定性重算)」):本模块只产出
结构化主张分解——分数由控制面确定性重算,故输出契约 SHALL NOT 含任何
分数字段。prompt 为全新撰写(上游无同构资产),不适用 prompts.py 逐字
移植纪律;重要性/支撑分级取值域与控制面 kongming.chat.scoring 的权重
表同源演化,两侧口径不得单边漂移。
降级契约:LLM 失败/输出不可解析/主张为空 → ScoringError,api 侧不落
评分仅记日志(不阻断问答)。
"""

import logging

from kongming_agent.graph.nodes import parse_summary_json
from kongming_agent.llm import LlmClient, LlmError

logger = logging.getLogger(__name__)

_MAX_CLAIMS = 12  # 与控制面解析截断同值(防御双闸)
_MAX_MATERIAL_LEN = 24_000  # 评审材料上限(api 侧已截断,此处防御二次上限)

_SYSTEM_PROMPT = (
    "你是严格的答案质量评审员。对照给定证据,把助手回答分解为可验证的事实主张清单,"
    "并判断每个要点是否被回答。只输出严格 JSON,不要解释,不要 markdown 标记。"
)

_USER_PROMPT_TEMPLATE = """对下列「助手回答」做质量评审,输出严格 JSON。

评审规则:
1. 主张=承载可对照证据验证的事实断言的语句;
   纯主观评价、建议性收尾、寒暄等非事实语句不得纳入 claims。
2. 每条主张标注 importance(critical/normal/minor):
   critical=核心结论,错误即答案失败;normal=重要支撑;minor=补充细节。
3. 每条主张对照「对照证据」标注
   support(fully_supported/partially_supported/unsupported):
   数值/实体须在证据中找到对应才算支撑;证据未提及不得推定为支撑。
   excerpt 填主张依据的证据原文短摘录(无证据留空)。
4. sub_questions=把用户问题按信息要点拆解为子问题清单
   (条数服从真实要点数,单一事实问题允许 1 条;
   禁止复述原问凑数,用户未问的元信息不构成要点),
   每条标注 answered(布尔,按所问对象口径校验——以其他口径替代视同未回应)。
5. claims 最多 {max_claims} 条,按重要度取舍。
6. 输出形如:{{"claims":[{{"text":"…","importance":"…",
   "support":"…","excerpt":"…"}}],
   "sub_questions":[{{"question":"…","answered":true}}]}}。
   禁止输出任何分数。

用户问题与助手回答:
{material}"""


class ScoringError(Exception):
    """评审失败(LLM 异常/输出不可解析/无有效主张)——api 侧不落评分。"""


def sanitize_decomposition(raw: dict[str, object]) -> dict[str, object]:
    """评审输出净化(纯函数):仅保留契约键,主张数截断——多余键
    (含评审模型若违规输出的分数字段)一律剥离,不向控制面透传。"""
    claims = raw.get("claims")
    sub_questions = raw.get("sub_questions")
    out: dict[str, object] = {
        "claims": claims[:_MAX_CLAIMS] if isinstance(claims, list) else [],
        "sub_questions": sub_questions if isinstance(sub_questions, list) else [],
    }
    return out


async def score_answer(
    material: str,
    llm: LlmClient,
    *,
    trace_id: str | None = None,  # 用量归因到本轮(api 透传)
    tenant_key: str | None = None,  # 用量租户维度
) -> dict[str, object]:
    """评审答案材料 → 结构化分解;任何失败抛 ScoringError(api 侧降级)。"""
    m = material.strip()[:_MAX_MATERIAL_LEN]
    if not m:
        raise ScoringError("空评审材料")
    try:
        raw = await llm.chat(
            _SYSTEM_PROMPT,
            _USER_PROMPT_TEMPLATE.format(max_claims=_MAX_CLAIMS, material=m),
            temperature=0.1,
            max_tokens=2000,
            task="ANSWER_SCORING",  # 任务路由(部署可指到性价比档)
            trace_id=trace_id,
            tenant_key=tenant_key,
        )
    except LlmError as e:
        raise ScoringError(f"LLM 调用失败: {e}") from e
    parsed = parse_summary_json(raw)
    if not parsed or not isinstance(parsed.get("claims"), list) or not parsed.get("claims"):
        raise ScoringError("评审输出不可解析或无有效主张")
    decomposition = sanitize_decomposition(parsed)
    logger.info("答案评审完成")
    return decomposition
