"""事件源适配器(031,零框架):平台事件格式 → 标准化目标输入。

custom:整 payload 透传;github:X-GitHub-Event + push/pull_request/issues
提要({event, repo, ref, summary})。
"""

from typing import Any

from kongming.trigger.models import CronError


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
            repo = str(repo_raw.get("full_name") or "")
        if event == "push":
            ref = str(payload.get("ref") or "")
            commits = payload.get("commits")
            n = len(commits) if isinstance(commits, list) else 0
            pusher = ""
            if isinstance(payload.get("pusher"), dict):
                pusher = str(payload["pusher"].get("name") or "")
            summary = f"push {n} commits by {pusher}"
        elif event == "pull_request":
            pr = payload.get("pull_request")
            if isinstance(pr, dict):
                ref = str(pr.get("number") or "")
                summary = f"PR #{ref} {pr.get('title', '')}"
        elif event == "issues":
            issue = payload.get("issue")
            if isinstance(issue, dict):
                ref = str(issue.get("number") or "")
                summary = f"issue #{ref} {issue.get('title', '')}"
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
