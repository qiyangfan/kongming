"""圆桌编排:参与者经编排器共享按轮追加的讨论记录,不直接互发消息。

主席共识判定、轮次上限与共享执行预算控制结束时机;未达共识与执行失败
如实保留,不转成成功结论。
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, cast

log = logging.getLogger(__name__)

# 圆桌轮次上限缺省(与 api 侧归一缺省同值同源;部署 cap 参数化)
DEFAULT_MAX_ROUNDS = 3
# 弃权标记(发言正文精确形态;宽松前缀形态亦接受,防止模型追加说明)
ABSTAIN_TEXT = "弃权"
# 立场标注前缀(发言末行;未标注如实记「未标注」)
STANCE_PREFIX = "立场"

# 内置角色章程:自研资产,非上游逐字移植——按圆桌纪律演进。
ROLE_CHARTERS: dict[str, str] = {
    "analyst": (
        "你是圆桌数据分析师(analyst):以数据为据给出结论与论据,"
        "主动用只读查询核实关键数字;引用的每个数字都必须来自你自己的查证或黑板转写。"
    ),
    "skeptic": (
        "你是圆桌质疑者(skeptic):审视其他参与者结论的口径、边界与反例,"
        "质疑必须有依据(数据或逻辑),不为反对而反对,也不因多数而附和。"
    ),
    "auditor": (
        "你是圆桌审计员(auditor):核查论断与证据引用是否一致、统计口径是否统一、"
        "有无过度推断;发现引用与数据不符时必须点名并给出核对结果。"
    ),
}
# 缺省名册;需与 API 侧 COLLAB_DEFAULT_ROSTER 保持一致。
DEFAULT_ROSTER: tuple[str, ...] = ("analyst", "skeptic")

_ROUNDTABLE_DISCIPLINE = (
    "\n\n# 圆桌纪律\n"
    "你正在一场多轮圆桌对辩中,与其他专家共同回答同一问题。每轮你有一次发言机会:\n"
    "1. 发言 = 分析 + 论据;需查证时使用你的只读查询工具,查证结果必须在发言中引用。\n"
    "2. 表名一律裸写(不带库名/schema 前缀,数据源自动路由);查询被拦截"
    "(BLOCKED)时按拦截原因换等价只读写法重试一次,仍失败则如实声明证据受限,"
    "不得改用「心算上下文数字」冒充查证。\n"
    "3. 发言最后一行必须是「立场:支持|反对|中性」——表明你相对当前圆桌主流结论的态度。\n"
    "4. 本轮确无新论据时,最终回答只写「弃权」二字(诚实弃权优于重复发言)。\n"
    "5. 只读数据查询之外不做任何超出授权的动作;结论必须可追溯到数据。\n"
)

_CHAIR_SYSTEM = (
    "你是圆桌主席。你不出具自己的立场,只判定圆桌是否已达成共识:"
    "全体参与者的结论是否已相互兼容且证据充分(残余分歧是否实质)。"
    "判定从严:存在实质口径或数值分歧即 false;仅有措辞差异方可 true。"
    '只输出 JSON:{"consensus": true/false, "reason": "判定理由(一句话)"}。'
    "不要输出任何其他文字。"
)
_CHAIR_USER = """# 圆桌问题
{subgoal}
# 发言转写(按轮)
{transcript}

请判定并输出:"""

# 参与者执行器(组合根以既有 ReAct 内核装配;签名=参与者序号+系统提示,
# 返回归一化终态 {verdict, summary, sql, has_execution};无授权/凭证参数)
ParticipantRunner = Callable[[int, str], Awaitable[dict[str, object]]]
# 轮次落账器(组合根装配 api 回调,尽力而为,不得抛出;主席判定占 worker_no=0;
# usage=该轮次 LLM 用量摘要,判定行恒空对象)
TurnRecorder = Callable[
    [int, int, str, dict[str, object], dict[str, object], dict[str, object]],
    Awaitable[None],
]


def parse_stance(text: str) -> str:
    """发言正文 → 立场标注(取最后一个「立场:」行;未标注如实返回)。
    标注行 = 前缀后紧跟分隔符(冒号/空白),叙述句(如「立场鲜明的反对」)
    不算标注;值在首个句读处截断,不吞后续正文。"""
    stance = "未标注"
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith(STANCE_PREFIX):
            continue
        rest = s[len(STANCE_PREFIX) :]
        if rest and rest[0] not in ":： \t":
            continue
        value = rest.lstrip(":： \t").strip()
        value = re.split(r"[.,，。;；!！?？]", value, maxsplit=1)[0].strip()
        if value:
            stance = value[:20]
    return stance


_ABSTAIN_NOTE_RE = re.compile(r"^弃权[。.\s]*([（(][^）)]{1,12}[)）])?[。.\s]*$")


def is_abstain(text: str) -> bool:
    """弃权判定:「弃权」精确形态或短括注形态(如「弃权(无新增论据)」);
    括注之外带实质内容的发言不判弃权——论据不得被静默吞进弃权行。"""
    return bool(_ABSTAIN_NOTE_RE.match(text.strip()))


def participant_system_prompt(
    persona: str, context: str, snapshot: str, round_no: int, max_rounds: int
) -> str:
    """参与者系统提示组装:人格/章程 + 圆桌纪律 + 上下文摘要 + 黑板快照。
    首轮无转写时省略黑板段;快照/上下文在此截断,不信任调用方传参体积。"""
    parts = [persona.strip(), _ROUNDTABLE_DISCIPLINE]
    info = f"# 圆桌信息\n本轮为第 {round_no}/{max_rounds} 轮;主席每轮后判定共识,触顶即收口。"
    if context.strip():
        info += f"\n# 任务上下文摘要\n{context[:800]}"
    parts.append(info)
    if snapshot.strip():
        parts.append(f"# 黑板转写(最近发言)\n{snapshot[:2000]}")
    return "\n\n".join(p for p in parts if p.strip())


def _snapshot(turns: list[dict[str, object]], cap: int = 2000) -> str:
    """黑板受控快照:最近 12 条转写的单行紧凑形态,总体积截断。"""
    lines: list[str] = []
    for t in turns[-12:]:
        kind = str(t.get("kind") or "")
        if kind == "verdict":
            lines.append(
                f"第{t.get('round_no')}轮 主席判定:consensus={t.get('consensus')}"
                f"({t.get('reason')})"
            )
            continue
        stance = str(t.get("stance") or "")
        tag = "弃权" if kind == "abstain" else stance
        lines.append(
            f"第{t.get('round_no')}轮 {t.get('label')}[{tag}]:{str(t.get('summary') or '')[:300]}"
        )
    return "\n".join(lines)[-cap:]


class RoundtableOrchestrator:
    """一次步骤内的圆桌编排(每步实例化;依赖显式注入便于剧本化测试)。

    roster:有效参与者名册(序号/角色/标签/人格已由组合根装配;运行期锁定);
    participant_runner:参与者执行器(一次受限 ReAct,签名见 ParticipantRunner);
    turn_recorder:轮次落账器(尽力而为);max_rounds:轮次硬顶;
    execution_budget:发言与查证执行预算池(None = 参与者数 × 轮次上限推导)。
    """

    def __init__(
        self,
        *,
        payload: dict[str, object],
        llm: Any,  # noqa: ANN401 —— LlmClient 显式类型在调用方;剧本化测试注入假体
        roster: list[dict[str, object]],
        participant_runner: ParticipantRunner,
        turn_recorder: TurnRecorder | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        execution_budget: int | None = None,
    ) -> None:
        self._payload = payload
        self._llm = llm
        self._roster = roster
        self._run_participant = participant_runner
        self._recorder = turn_recorder
        rounds = max(1, max_rounds)
        self._max_rounds = rounds
        budget = len(roster) * rounds if execution_budget is None else max(0, execution_budget)
        self._budget_left = budget

    async def run(self) -> dict[str, object]:
        """执行圆桌:逐轮(参与者发言 → 主席判定)→ 终止通道触发即诚实收口。"""
        subgoal = str(self._payload.get("subgoal") or "")
        context = str(self._payload.get("context_summary") or "")
        if len(self._roster) < 2:
            # 名册有效规模不足:不开桌(fail-closed),部分完成语义如实披露
            return {
                "verdict": "ERROR",
                "summary": "[]",
                "conclusions": [],
                "pending_subgoals": [subgoal],
                "dissent": True,
                "worker_count": len(self._roster),
                "reservation": "有效参与者不足(零工具 fail-closed 或名册缺额),未开桌",
                "rounds_used": 0,
                "consensus": False,
            }
        if self._budget_left <= 0:
            return {
                "verdict": "ERROR",
                "summary": "[]",
                "conclusions": [],
                "pending_subgoals": [subgoal],
                "dissent": True,
                "worker_count": len(self._roster),
                "reservation": "执行预算继承不足,未开桌",
                "rounds_used": 0,
                "consensus": False,
            }
        turns: list[dict[str, object]] = []
        notes: list[str] = []
        consensus = False
        rounds_used = 0
        for round_no in range(1, self._max_rounds + 1):
            rounds_used = round_no
            snapshot = _snapshot(turns)
            all_abstained = True
            budget_short = False
            for p in self._roster:
                if self._budget_left <= 0:
                    budget_short = True
                    break
                self._budget_left -= 1
                sys_prompt = participant_system_prompt(
                    str(p.get("persona") or ""),
                    context,
                    snapshot,
                    round_no,
                    self._max_rounds,
                )
                final = await self._run_turn(p, round_no, sys_prompt)
                turns.append(final)
                if self._recorder is not None:
                    await self._recorder(
                        round_no,
                        int(cast("int", p.get("no") or 0)),
                        str(final.get("kind") or ""),
                        {
                            "stance": final.get("stance"),
                            "summary": final.get("summary"),
                            "verdict": final.get("verdict"),
                        },
                        {"sql": final.get("sql"), "trace_id": final.get("trace_id")},
                        cast("dict[str, object]", final.get("usage") or {}),
                    )
                if str(final.get("kind") or "") != "abstain":
                    all_abstained = False
            if budget_short:
                notes.append(f"执行预算耗尽,第 {round_no} 轮未完整(收口如实披露)")
                break
            if all_abstained:
                notes.append(f"第 {round_no} 轮全员弃权,提前收口(无可新增论据)")
                break
            verdict = await self._chair_round(subgoal, turns)
            if verdict is not None:
                turns.append(verdict)
                if self._recorder is not None:
                    await self._recorder(
                        round_no,
                        0,
                        "verdict",
                        {
                            "consensus": verdict.get("consensus"),
                            "reason": verdict.get("reason"),
                        },
                        {},
                        {},
                    )
                if bool(verdict.get("consensus")):
                    consensus = True
                    break
            if round_no == self._max_rounds:
                notes.append(f"轮次上限 {self._max_rounds} 触顶,未共识收口")

        conclusions = self._conclusions_of(turns)
        final_verdict, reservation = self._final_verdict(turns, consensus, notes)
        return {
            "verdict": final_verdict,
            "summary": json.dumps(conclusions, ensure_ascii=False),
            "conclusions": conclusions,
            "pending_subgoals": [],
            "dissent": not consensus,
            "worker_count": len(self._roster),
            "reservation": reservation,
            "rounds_used": rounds_used,
            "consensus": consensus,
        }

    async def _run_turn(
        self, p: dict[str, object], round_no: int, sys_prompt: str
    ) -> dict[str, object]:
        """一次参与者执行 → 转写行(弃权/发言;执行失败如实记 ERROR 不中断)。"""
        no = int(cast("int", p.get("no") or 0))
        try:
            final = await self._run_participant(no, sys_prompt)
        except Exception as e:  # noqa: BLE001 —— 参与者失败如实记录,不中断圆桌
            log.warning("圆桌参与者执行失败(no=%s): %s", no, e)
            final = {"verdict": "ERROR", "summary": f"参与者执行失败: {type(e).__name__}"}
        summary = str(final.get("summary") or "")[:2000]
        abstain = is_abstain(summary)
        return {
            "round_no": round_no,
            "no": no,
            "label": str(p.get("label") or ""),
            "role": str(p.get("role") or ""),
            "kind": "abstain" if abstain else "speech",
            "stance": "" if abstain else parse_stance(summary),
            "verdict": "ABSTAIN" if abstain else str(final.get("verdict") or "ERROR"),
            "summary": summary,
            "sql": str(final.get("sql") or "")[:2000],
            "has_execution": bool(final.get("has_execution")),
            "trace_id": str(final.get("trace_id") or ""),
            "usage": dict(cast("dict[str, object]", final.get("usage") or {})),
        }

    async def _chair_round(
        self, subgoal: str, turns: list[dict[str, object]]
    ) -> dict[str, object] | None:
        """主席判定(每轮一次):结构化共识裁决;畸形/异常按未共识处理并披露。"""
        speeches = [
            f"第{t.get('round_no')}轮 {t.get('label')}[{t.get('stance')}]:"
            f"{str(t.get('summary') or '')[:400]}"
            for t in turns
            if str(t.get("kind") or "") == "speech"
        ]
        try:
            raw = await self._llm.chat(
                _CHAIR_SYSTEM,
                _CHAIR_USER.format(subgoal=subgoal[:1000], transcript="\n".join(speeches)[-4000:]),
                temperature=0.0,
            )
        except Exception as e:  # noqa: BLE001 —— 判定异常如实披露,按未共识处理
            log.warning("圆桌主席判定异常: %s", e)
            return {
                "round_no": turns[-1].get("round_no"),
                "consensus": False,
                "reason": "主席判定异常(按未共识处理)",
                "kind": "verdict",
            }
        obj = _json_object(raw)
        if obj is None:
            return {
                "round_no": turns[-1].get("round_no"),
                "consensus": False,
                "reason": "主席判定输出畸形(按未共识处理)",
                "kind": "verdict",
            }
        return {
            "round_no": turns[-1].get("round_no"),
            "consensus": bool(obj.get("consensus")),
            "reason": str(obj.get("reason") or "")[:300],
            "kind": "verdict",
        }

    @staticmethod
    def _conclusions_of(turns: list[dict[str, object]]) -> list[dict[str, object]]:
        """各参与者最终结论(最后实质发言;全程弃权如实记 ABSTAIN 条目,
        弃权行不遮蔽早前的实质发言)。"""
        order: list[int] = []
        meta: dict[int, tuple[str, str]] = {}
        last_speech: dict[int, dict[str, object]] = {}
        for row in turns:
            no = int(cast("int", row.get("no") or 0))
            kind = str(row.get("kind") or "")
            if kind == "verdict":
                continue
            if no not in meta:
                meta[no] = (str(row.get("label") or ""), str(row.get("role") or ""))
                order.append(no)
            if kind == "speech":
                last_speech[no] = row
        out: list[dict[str, object]] = []
        for no in order:
            label, role = meta[no]
            t: dict[str, object] | None = last_speech.get(no)
            if t is None:
                out.append(
                    {
                        "participant": label,
                        "role": role,
                        "stance": "弃权",
                        "verdict": "ABSTAIN",
                        "summary": "全程弃权(无实质发言)",
                        "sql": "",
                    }
                )
                continue
            out.append(
                {
                    "participant": label,
                    "role": role,
                    "stance": str(t.get("stance") or ""),
                    "verdict": str(t.get("verdict") or ""),
                    "summary": str(t.get("summary") or "")[:2000],
                    "sql": str(t.get("sql") or "")[:2000],
                }
            )
        return out

    @staticmethod
    def _final_verdict(
        turns: list[dict[str, object]], consensus: bool, notes: list[str]
    ) -> tuple[str, str | None]:
        """收口裁决(诚实口径):共识不洗白执行失败;全程无数据查证的纯文本
        对辩不构成完成证据;未共识如实附保留意见(分歧不调和)。"""
        speeches = [t for t in turns if str(t.get("kind") or "") == "speech"]
        has_evidence = any(bool(t.get("has_execution")) for t in speeches)
        if not speeches:
            return "ERROR", "圆桌无任何实质发言(全员弃权或执行失败)"
        if not has_evidence:
            return "ERROR", "圆桌全程无数据查证,纯文本对辩不构成完成证据"
        attempted = [t for t in speeches if str(t.get("verdict") or "") != "ABSTAIN"]
        cross = (
            all(str(t.get("verdict") or "") == "PASS" for t in attempted) if attempted else False
        )
        reservation: str | None = None
        if notes:
            reservation = ";".join(dict.fromkeys(notes))[:1000]
        if consensus and cross:
            return "PASS", reservation
        if consensus and not cross:
            # 共识判定与执行证据冲突:按证据口径收口(共识不洗白失败)
            return "ERROR", ("共识判定与执行证据冲突,按证据口径收口:" + (reservation or ""))[:1000]
        return ("ERROR" if not cross else "PASS"), reservation


def _json_object(raw: str) -> dict[str, object] | None:
    """LLM 输出 → JSON 对象(剥围栏+首尾花括号;畸形返回 None)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return cast("dict[str, object]", obj) if isinstance(obj, dict) else None


async def run_roundtable(
    *,
    payload: dict[str, object],
    llm: Any,  # noqa: ANN401
    roster: list[dict[str, object]],
    participant_runner: ParticipantRunner,
    turn_recorder: TurnRecorder | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    execution_budget: int | None = None,
) -> dict[str, object]:
    """模块入口(组合根薄壳调用)。不持 api 句柄:参与者内工具调用经既有
    ReAct 回调,轮次转写经组合根装配的落账器回写(尽力而为)。"""
    return await RoundtableOrchestrator(
        payload=payload,
        llm=llm,
        roster=roster,
        participant_runner=participant_runner,
        turn_recorder=turn_recorder,
        max_rounds=max_rounds,
        execution_budget=execution_budget,
    ).run()
