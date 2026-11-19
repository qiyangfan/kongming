"""事件源适配器(零框架):平台事件格式 → 标准化目标输入。

custom:整 payload 透传;github:X-GitHub-Event + push/pull_request/issues
提要({event, repo, ref, summary})。
邮件摘要适配器 normalize_email——RFC 822 字节 → 四字段白名单摘要
({from, subject, date, text_preview}),长度上限截断;邮件头可被构造出
注入载荷,白名单+截断+输入护栏三层收敛,不透传原始头。
"""

from email import policy
from email.parser import BytesParser
from typing import Any, cast

from kongming.trigger.models import CronError

# 邮件摘要字段白名单(四字段定死)与长度上限(第二层防线)
EMAIL_SUMMARY_FIELDS = ("from", "subject", "date", "text_preview")
MAX_FROM_LEN = 256
MAX_SUBJECT_LEN = 256
MAX_DATE_LEN = 64
MAX_PREVIEW_LEN = 1000


class AdapterError(Exception):
    """适配器解析失败(payload 形态不符)。"""


def normalize(adapter: str, payload: dict[str, Any], event_header: str = "") -> dict[str, Any]:
    """标准化入口(custom/github);未知适配器抛 AdapterError(保存期已拦)。"""
    if adapter == "custom":
        return dict(payload)
    if adapter == "github":
        return _normalize_github(payload, event_header)
    raise AdapterError(f"未知适配器: {adapter}")


def _as_dict(value: object) -> dict[str, object] | None:
    """对象收窄;非对象返回 None(载荷形态容错)。"""
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def _github_ref_summary(event: str, payload: dict[str, Any]) -> tuple[str, str]:
    """按事件类型取 (ref, summary);未知事件只留空提要。"""
    if event == "push":
        commits = payload.get("commits")
        n = len(cast("list[object]", commits)) if isinstance(commits, list) else 0
        pusher = (_as_dict(payload.get("pusher")) or {}).get("name") or ""
        return str(payload.get("ref") or ""), f"push {n} commits by {pusher}"
    inner_key = {"pull_request": "pull_request", "issues": "issue"}.get(event)
    if inner_key is None:
        return "", ""
    inner = _as_dict(payload.get(inner_key))
    if inner is None:
        return "", ""  # 提载体缺失:无可摘要(不拼空壳前缀)
    ref = str(inner.get("number") or "")
    label = {"pull_request": "PR", "issues": "issue"}[event]
    return ref, f"{label} #{ref} {inner.get('title', '')}"


def _normalize_github(payload: dict[str, Any], event_header: str) -> dict[str, Any]:
    """GitHub 提要:事件头优先,缺省取载荷 _event。"""
    event = event_header or str(payload.get("_event") or "")
    if not event:
        raise AdapterError("缺 X-GitHub-Event 头")
    repo = (_as_dict(payload.get("repository")) or {}).get("full_name") or ""
    ref, summary = _github_ref_summary(event, payload)
    return {"event": event, "repo": str(repo), "ref": ref, "summary": summary}


def validate_cron(expr: str) -> None:
    """保存期校验入口(CronError 透传)。"""
    from kongming.trigger.models import parse_cron

    try:
        parse_cron(expr)
    except CronError:
        raise


# ── 邮件摘要适配器 ──


def _clean_header(value: object) -> str:
    """头部值白名单化:字符串化并剔除控制字符(RFC 2047 解码由
    policy.default 承担;换行折叠由解析器展开),防头部注入载荷透传。"""
    s = str(value) if value is not None else ""
    return "".join(ch for ch in s if ch == "\t" or ord(ch) >= 32)


def _text_preview(msg: Any) -> str:
    """首段 text/plain(非附件)的正文预览;HTML 富正文不提取;
    畸形分段跳过,永不抛。"""
    for part in msg.walk():
        if part.get_content_type() != "text/plain":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        try:
            text = part.get_content()
        except Exception:  # noqa: BLE001, S112  # 坏分段(编码/charset)降级跳过——单封坏邮件不中断轮询
            continue
        if isinstance(text, str):
            return " ".join(text.split())  # 折叠空白(含软换行)
    return ""


def normalize_email(raw: bytes) -> dict[str, str]:
    """邮件原始字节 → 四字段白名单摘要;畸形输入降级为空字段,永不抛
    (轮询面永不因单封坏邮件中断)。产出即摘要全量——调用方不再透传
    任何原始头/完整正文。"""
    out = {f: "" for f in EMAIL_SUMMARY_FIELDS}
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        out["from"] = _clean_header(msg.get("From"))[:MAX_FROM_LEN]
        out["subject"] = _clean_header(msg.get("Subject"))[:MAX_SUBJECT_LEN]
        out["date"] = _clean_header(msg.get("Date"))[:MAX_DATE_LEN]
        out["text_preview"] = _text_preview(cast("Any", msg))[:MAX_PREVIEW_LEN]
    except Exception:  # noqa: BLE001  # 解析总闸:任何畸形收敛空摘要
        return {f: "" for f in EMAIL_SUMMARY_FIELDS}
    return out
