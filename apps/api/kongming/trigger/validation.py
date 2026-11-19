"""trigger 保存期校验(零框架纯函数):task 目标模板、cron 表达式与 IMAP 配置。
非法一律抛 TriggerValidationError(fail-fast,错误文案即对外 detail)。"""

import datetime as dt
import re
from typing import Any, cast

from kongming.trigger.errors import TriggerValidationError
from kongming.trigger.models import CronError, parse_cron

TG_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Shanghai(固定)

TASK_TARGET = "task"

# 邮件轮询节流下限封顶(防轮询风暴)与每轮处理数上限
# (剩余下轮续传,游标语义天然支持)
MIN_POLL_INTERVAL_SEC = 30
MAX_MESSAGES_PER_POLL = 20

_TASK_TEMPLATE_KEYS = frozenset(
    {
        "goal_template",
        "datasource_key",
        "budget",
        "topology",
        "confirm_auto",
        "thread_field",
        "filter",
    }
)
_BUDGET_KEYS = ("max_steps", "max_tokens", "max_seconds")
_TOPOLOGY_KINDS = ("decomposition", "roundtable")
_IMAP_FIELDS = (
    "imap_host",
    "imap_port",
    "imap_tls",
    "imap_user",
    "imap_folder",
    "imap_poll_interval_sec",
)


def now() -> dt.datetime:
    return dt.datetime.now(_TZ)


def first_message(checks: tuple[str, ...]) -> str:
    """取首个非空校验文案;全部通过返回空串。"""
    return next((c for c in checks if c), "")


def bounded_str_len(value: str, lo: int, hi: int) -> bool:
    """长度区间判定(含两端)。"""
    return lo <= len(value) <= hi


def validate_task_template(template: object) -> dict[str, Any]:
    """task 目标模板保存期校验:非法即 TriggerValidationError。

    形态:goal_template(必填 ≤4000)/datasource_key(必填 ≤64)/
    budget(可选,三上限键)/topology(可选,kind ∈ 分工|圆桌)/
    confirm_auto(可选 bool,缺省 False)/thread_field(可选 ≤64)/
    filter(可选 {actions:[…], equals:{k:v}})。越界成员与值域
    一律 400(fail-fast),Bot 存在性在派发期经既有声明路径复验。"""
    if not isinstance(template, dict):
        raise TriggerValidationError("task 目标须给任务模板(task_template)")
    tpl = cast("dict[str, Any]", template)
    unknown = set(tpl) - _TASK_TEMPLATE_KEYS
    if unknown:
        raise TriggerValidationError(f"task_template 未知字段: {sorted(unknown)}")
    goal = tpl.get("goal_template")
    if not isinstance(goal, str) or not bounded_str_len(goal.strip(), 1, 4000):
        raise TriggerValidationError("task_template.goal_template 须为 1–4000 字文本")
    ds = tpl.get("datasource_key")
    if not isinstance(ds, str) or not bounded_str_len(ds, 1, 64):
        raise TriggerValidationError("task_template.datasource_key 须为 1–64 字符")
    confirm = tpl.get("confirm_auto", False)
    thread_field = tpl.get("thread_field")
    if not isinstance(confirm, bool):
        raise TriggerValidationError("task_template.confirm_auto 须为布尔")
    if thread_field is not None and (
        not isinstance(thread_field, str) or not bounded_str_len(thread_field, 1, 64)
    ):
        raise TriggerValidationError("task_template.thread_field 须为 1–64 字符字段名")
    msg = first_message(
        (
            _check_budget(tpl.get("budget")),
            _check_topology(tpl.get("topology")),
            _check_filter(tpl.get("filter")),
        )
    )
    if msg:
        raise TriggerValidationError(msg)
    out = dict(tpl)
    out["goal_template"] = goal.strip()
    out["confirm_auto"] = confirm
    return out


def _check_budget(budget: object) -> str:
    """budget 三上限键:缺省放过,给出即须正整数(bool 不算整数)。"""
    if budget is None:
        return ""
    if not isinstance(budget, dict):
        return "task_template.budget 须为对象"
    budget_d = cast("dict[str, Any]", budget)
    for k in _BUDGET_KEYS:
        v = budget_d.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v <= 0):
            return f"task_template.budget.{k} 须为正整数"
    return ""


def _check_topology(topology: object) -> str:
    """topology 只认 kind=decomposition/roundtable 的对象。"""
    kind = cast("dict[str, Any]", topology).get("kind") if isinstance(topology, dict) else None
    if topology is None or str(kind or "") in _TOPOLOGY_KINDS:
        return ""
    return "task_template.topology.kind 须为 decomposition 或 roundtable"


def _check_filter(flt: object) -> str:
    """filter 两键:actions 为非空字符串数组(≤10),equals 为 {字段: 标量}(≤10)。"""
    if flt is None:
        return ""
    if not isinstance(flt, dict):
        return "task_template.filter 须为对象"
    flt_d = cast("dict[str, Any]", flt)
    if not _actions_ok(flt_d.get("actions")):
        return "task_template.filter.actions 须为非空字符串数组(≤10)"
    if not _equals_ok(flt_d.get("equals")):
        return "task_template.filter.equals 须为 {字段: 标量} 对象(≤10)"
    return ""


def _actions_ok(actions: object) -> bool:
    """actions 缺省放过;给出时须 1–10 个非空字符串。"""
    if actions is None:
        return True
    if not isinstance(actions, list):
        return False
    items = cast("list[object]", actions)
    if not bounded_str_len("x" * len(items), 1, 10):
        return False
    return all(isinstance(a, str) and a for a in items)


def _equals_ok(equals: object) -> bool:
    """equals 缺省放过;给出时须 1–10 个「非空键: str/int/bool 值」项。"""
    if equals is None:
        return True
    if not isinstance(equals, dict):
        return False
    items = cast("dict[str, Any]", equals)
    if not bounded_str_len("x" * len(items), 1, 10):
        return False
    return all(k and isinstance(v, (str, int, bool)) for k, v in items.items())


def parse_cron_or_400(expr: object) -> None:
    """cron 表达式保存期校验;CronError 原文案转 400。"""
    if not expr:
        raise TriggerValidationError("CRON 触发器须给 cron_expr")
    try:
        parse_cron(str(expr))
    except CronError as e:
        raise TriggerValidationError(str(e)) from e


def next_fire_iso_or_400(expr: object) -> str:
    """下次触发时刻;计算失败(永不触发/366 天外)同属保存期 400。"""
    from kongming.trigger.models import next_fire

    try:
        return next_fire(str(expr), now()).isoformat()
    except CronError as e:
        raise TriggerValidationError(str(e)) from e


def imap_port_ok(port: object) -> bool:
    """IMAP 端口值域(1–65535)。"""
    try:
        return 1 <= int(cast("Any", port)) <= 65535
    except (TypeError, ValueError):
        return False


def imap_host_msg(host: object) -> str:
    """IMAP 主机必填文案(空串=通过)。"""
    return "" if str(host or "").strip() else "EMAIL 触发器须给 IMAP 主机"


def imap_user_msg(user: object) -> str:
    """IMAP 账号必填文案(空串=通过)。"""
    return "" if str(user or "").strip() else "EMAIL 触发器须给 IMAP 账号"


def imap_port_msg(port: object) -> str:
    """IMAP 端口值域文案(空串=通过)。"""
    return "" if imap_port_ok(port) else "IMAP 端口须为 1–65535"


def check_imap_create(host: object, user: object, password: object, port: object) -> None:
    """创建期 IMAP 必填项与端口校验(主机/账号/凭证缺一即 400)。"""
    msg = first_message((imap_host_msg(host), imap_user_msg(user)))
    if not password:
        msg = "EMAIL 触发器须给 IMAP 凭证(密码)"
    if not msg:
        msg = imap_port_msg(port)  # pydantic 已拦;领域层同源兜底
    if msg:
        raise TriggerValidationError(msg)


def prepare_imap_update(kw: dict[str, Any]) -> str:
    """更新期邮件配置规整:主机/账号就地 strip,端口/文件夹/轮询间隔缺省补值。

    返回待加密封口(空=凭证保持现值,调用方不重加密)。"""
    msg = first_message(
        (
            imap_host_msg(kw.get("imap_host")),
            imap_user_msg(kw.get("imap_user")),
            imap_port_msg(kw.get("imap_port", 993)),
        )
    )
    if msg:
        raise TriggerValidationError(msg)
    kw["imap_host"] = str(kw["imap_host"]).strip()
    kw["imap_user"] = str(kw["imap_user"]).strip()
    kw["imap_folder"] = str(kw.get("imap_folder") or "").strip() or "INBOX"
    kw["imap_poll_interval_sec"] = max(
        MIN_POLL_INTERVAL_SEC, int(kw.get("imap_poll_interval_sec") or 300)
    )
    return str(kw.pop("imap_password", "") or "")


def strip_imap_fields(kw: dict[str, Any]) -> None:
    """非 EMAIL 行剥除邮件配置列,防更新误覆盖为邮件形态的值。"""
    for f in _IMAP_FIELDS:
        kw.pop(f, None)
