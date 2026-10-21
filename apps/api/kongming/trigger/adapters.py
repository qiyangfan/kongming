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
        event = event_header or str(payload.get("_event") or "")
        repo = ""
        ref = ""
        summary = ""
        repo_raw = payload.get("repository")
        if isinstance(repo_raw, dict):
            # isinstance 收窄产物键型未知,收窄键型后再取值(仅类型断言)
            repo_d = cast("dict[str, object]", repo_raw)
            repo = str(repo_d.get("full_name") or "")
        if event == "push":
            ref = str(payload.get("ref") or "")
            commits = payload.get("commits")
            n = len(cast("list[object]", commits)) if isinstance(commits, list) else 0
            pusher = ""
            if isinstance(payload.get("pusher"), dict):
                pusher = str(cast("dict[str, object]", payload["pusher"]).get("name") or "")
            summary = f"push {n} commits by {pusher}"
        elif event == "pull_request":
            pr = payload.get("pull_request")
            if isinstance(pr, dict):
                pr_d = cast("dict[str, object]", pr)
                ref = str(pr_d.get("number") or "")
                summary = f"PR #{ref} {pr_d.get('title', '')}"
        elif event == "issues":
            issue = payload.get("issue")
            if isinstance(issue, dict):
                issue_d = cast("dict[str, object]", issue)
                ref = str(issue_d.get("number") or "")
                summary = f"issue #{ref} {issue_d.get('title', '')}"
        if not event:
            raise AdapterError("缺 X-GitHub-Event 头")
        return {"event": event, "repo": repo, "ref": ref, "summary": summary}
    raise AdapterError(f"未知适配器: {adapter}")


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
