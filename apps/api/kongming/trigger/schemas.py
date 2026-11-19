"""trigger 请求/响应模型与行投影(模型类名冻结:OpenAPI components 单源)。"""

from typing import Any

from pydantic import BaseModel, Field


class TriggerIn(BaseModel):
    tg_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(pattern="^(CRON|WEBHOOK|EMAIL)$")
    target_type: str = Field(pattern="^(workflow|bot|task)$")
    # task 目标不受理 target_key(配置在任务模板);workflow/bot 须非空(service 校验)
    target_key: str = Field(default="", max_length=64)
    cron_expr: str | None = Field(default=None, max_length=64)
    min_interval_sec: int = Field(default=60, ge=0, le=86400)
    adapter: str = Field(default="custom", pattern="^(custom|github)$")
    notify_url: str = Field(default="", max_length=512)
    secret: str = Field(default="", max_length=256)
    # 失败重试次数上限(0–5;0=不重试,缺省)
    max_retries: int = Field(default=0, ge=0, le=5)
    # task 目标任务模板(goal/数据源/预算/拓扑/确认模式/过滤/线程字段)
    task_template: dict[str, Any] | None = None
    # 邮件形态配置(凭证明文只进不出,即刻加密落库)
    imap_host: str = Field(default="", max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_tls: bool = True
    imap_user: str = Field(default="", max_length=255)
    imap_password: str = Field(default="", max_length=256)
    imap_folder: str = Field(default="INBOX", max_length=255)
    imap_poll_interval_sec: int = Field(default=300, ge=1, le=86400)


class TriggerUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    target_type: str = Field(pattern="^(workflow|bot|task)$")
    target_key: str = Field(default="", max_length=64)
    cron_expr: str | None = Field(default=None, max_length=64)
    min_interval_sec: int = Field(default=60, ge=0, le=86400)
    adapter: str = Field(default="custom", pattern="^(custom|github)$")
    notify_url: str = Field(default="", max_length=512)
    # 密钥轮换(空=保持现值;更新 webhook 密钥后旧签名即失效)
    secret: str = Field(default="", max_length=256)
    # 失败重试次数上限(0–5;0=不重试,缺省)
    max_retries: int = Field(default=0, ge=0, le=5)
    # task 目标任务模板(形态或模板任一变更即保存期校验)
    task_template: dict[str, Any] | None = None
    # 邮件形态配置(凭证空框=保持现值,与 webhook secret 同口径)
    imap_host: str = Field(default="", max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_tls: bool = True
    imap_user: str = Field(default="", max_length=255)
    imap_password: str = Field(default="", max_length=256)
    imap_folder: str = Field(default="INBOX", max_length=255)
    imap_poll_interval_sec: int = Field(default=300, ge=1, le=86400)


class TriggerOut(BaseModel):
    """触发器列表中的条目。"""

    id: str
    tg_key: str
    name: str
    type: str
    target_type: str
    target_key: str
    cron_expr: str | None = None
    min_interval_sec: int
    webhook_path: str | None = None
    adapter: str
    notify_url: str = ""
    enabled: bool
    has_secret: bool
    last_fired_at: str | None = None
    next_fire_at: str | None = None
    # 失败重试次数上限(0=不重试)
    max_retries: int = 0
    # task 目标任务模板回显(表单回显;非 task 目标为 null)
    task_template: dict[str, Any] | None = None
    # 邮件形态配置(凭证仅掩码口径,永不回显明文)
    imap_host: str = ""
    imap_port: int = 993
    imap_tls: bool = True
    imap_user: str = ""
    imap_folder: str = "INBOX"
    imap_poll_interval_sec: int = 300
    has_imap_credential: bool = False


class TriggerRunOut(BaseModel):
    id: str
    status: str
    error: str | None = None
    duration_ms: int
    target_run_ref: str | None = None
    created_at: str | None = None
    # 重试链:尝试序号 + 首跑关联 + 挂起重试到期时刻
    attempt: int = 1
    retry_of_run_id: str | None = None
    next_retry_at: str | None = None
    # 邮件触发的标准化摘要 JSON(原文不落库,只留白名单摘要)
    summary: str = ""


class TriggerRunsPageOut(BaseModel):
    total: int
    items: list[TriggerRunOut]


class CronPreviewOut(BaseModel):
    """cron 预览(与创建/调度同源的解析计算,前端防抖消费)。"""

    next: list[str]


def to_trigger_row(row: dict[str, Any]) -> dict[str, Any]:
    """触发器行 → 列表回显投影(凭证仅掩码口径)。"""
    return {
        "id": str(row["id"]),
        "tg_key": row["tg_key"],
        "name": row["name"],
        "type": row["type"],
        "target_type": row["target_type"],
        "target_key": row["target_key"],
        "cron_expr": row.get("cron_expr"),
        "min_interval_sec": row["min_interval_sec"],
        "webhook_path": (str(row["webhook_path"]) if row.get("webhook_path") else None),
        "adapter": row["adapter"],
        "notify_url": row.get("notify_url") or "",
        "enabled": bool(row["enabled"]),
        "has_secret": bool(row.get("secret_cipher")),
        "last_fired_at": (row["last_fired_at"].isoformat() if row.get("last_fired_at") else None),
        "next_fire_at": (row["next_fire_at"].isoformat() if row.get("next_fire_at") else None),
        "max_retries": int(row.get("max_retries") or 0),
        "task_template": row.get("task_template"),
        # 邮件配置回显(凭证仅掩码口径 has_imap_credential)
        "imap_host": str(row.get("imap_host") or ""),
        "imap_port": int(row.get("imap_port") or 993),
        "imap_tls": bool(row.get("imap_tls")),
        "imap_user": str(row.get("imap_user") or ""),
        "imap_folder": str(row.get("imap_folder") or "INBOX"),
        "imap_poll_interval_sec": int(row.get("imap_poll_interval_sec") or 300),
        "has_imap_credential": bool(row.get("imap_credential")),
    }
