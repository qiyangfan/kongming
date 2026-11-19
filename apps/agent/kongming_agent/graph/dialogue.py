"""对话复语直通:闭合词表 + 全等判定,零依赖纯数据/纯函数。"""

import logging
import re
import threading

log = logging.getLogger(__name__)

# 知识未覆盖哨兵(生成侧如实说明的唯一出口);短路零执行零重试
_NO_DATA_SENTINEL = "NO_RELEVANT_TABLE"

# 规范化:去首尾与内部空白、去中英文句读、拉丁字母小写。
# 全等匹配,SHALL NOT 包含/前缀/语义相似——"取消订单""确认切换"照常补全。
_PUNCT_RE = re.compile(r"[\s,。，、！？!?,.;:；：…\-—_\"'“”‘’()（）\[\]【】/\\~·「」『』《》〈〉]+")

# 闭合词表(规范化形态;随代码发布,不做管理台热配)。三族:
# stop=否定/终止 · ack=应答/致谢 · greeting=招呼。多词英文条目去空格后入表
# (never mind→nevermind)。族别决定直通应答文案(spec「对话复语不进取数补全」)。
_DIALOGUE_REPLY_FAMILIES: dict[str, frozenset[str]] = {
    "stop": frozenset(
        {
            "不用了",
            "不需要",
            "不必",
            "算了",
            "取消",
            "终止",
            "停",
            "no",
            "nope",
            "cancel",
            "nevermind",
        }
    ),
    "ack": frozenset(
        {
            "好的",
            "好",
            "知道了",
            "明白",
            "收到",
            "谢谢",
            "多谢",
            "thanks",
            "thankyou",
            "gotit",
            "ok",
            "okay",
            "nothanks",  # 「NO, thanks」形态(spec 场景:句读大小写变体)
            "nothankyou",
        }
    ),
    "greeting": frozenset({"你好", "您好", "hi", "hello", "hey"}),
}
DIALOGUE_REPLY_WORDS: frozenset[str] = frozenset(
    w for ws in _DIALOGUE_REPLY_FAMILIES.values() for w in ws
)

# 直通应答文案(族别静态表,零模型调用;招呼/致谢 SHALL NOT 以数据缺失话术作答)
CHITCHAT_REPLIES: dict[str, str] = {
    "stop": "好的,已停止本轮查询。需要继续分析数据时随时告诉我。",
    "ack": "不客气!如需继续分析数据,随时告诉我。",
    "greeting": "你好!请描述你想分析的数据问题,例如「上月销售额是多少」。",
}
# 语义层兜底统一文案(意图路由帧判定的词表漏网闲聊;不区分族别,design D4)
CHITCHAT_SEMANTIC_REPLY = "如需分析数据,请随时描述你想查的问题。"


def reply_family(question: str) -> str:
    """词表成员 → 族别(直通命中后取文案用;非成员回退 ack,不单独暴露)。"""
    normalized = normalize_dialogue_reply(question)
    return next((f for f, ws in _DIALOGUE_REPLY_FAMILIES.items() if normalized in ws), "ack")


def normalize_dialogue_reply(text: str) -> str:
    """复语规范化(纯函数):去首尾与内部空白、去中英文句读、拉丁字母小写。"""
    return _PUNCT_RE.sub("", text).lower()


def is_dialogue_reply(question: str) -> bool:
    """复语判定(纯函数):规范化后全等于闭合词表成员。"""
    return normalize_dialogue_reply(question) in DIALOGUE_REPLY_WORDS


# 复语直通可观测计数(sqlguard.snapshot_stats 同款形态;零新采集通道)
_passthrough_lock = threading.Lock()
_passthrough_stats = {"dialogue_reply_passthrough_total": 0}


def snapshot_passthrough_stats() -> dict[str, int]:
    """复语直通命中计数快照(可观测:词表宽严调整依据)。"""
    with _passthrough_lock:
        return dict(_passthrough_stats)


def note_dialogue_passthrough(trace_id: str | None) -> None:
    """命中入账 + 随 trace 日志下发(复用既有日志关联通道,零新采集)。"""
    with _passthrough_lock:
        _passthrough_stats["dialogue_reply_passthrough_total"] += 1
    log.info("071 复语直通命中 trace_id=%s", trace_id)


def is_no_data_sentinel(raw: str) -> bool:
    """哨兵识别(纯函数):剥包裹标记后大小写折叠全等;子串出现不触发
    (夹在解释句中的哨兵按普通输出走既有防御链,防火墙兜底)。"""
    text = raw.strip().strip("`").strip()
    if text.lower().startswith("sql\n"):
        text = text[4:]
    return text.strip("`").strip().lower() == _NO_DATA_SENTINEL.lower()
