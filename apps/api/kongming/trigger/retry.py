"""触发器失败重试判定(101):纯函数,零 IO 零框架(领域纯净契约)。

规则收敛(spec「失败重试策略」):FAILED 且剩余次数>0 → 安排(返回到期
时刻);SUCCESS/SKIPPED/次数耗尽 → 链终态(返回 None,不重试)——SKIPPED
是并发保护语义不是执行失败,永不重试。退避为部署级固定间隔(design
裁决:不做指数退避/抖动;次数上限属触发器 owner,间隔属部署)。
"""

import datetime as dt

# max_retries 允许上限(与 DDL CHECK ck_sys_trigger_def_max_retries 同值域)
MAX_RETRIES_LIMIT = 5
# 部署级固定退避缺省(秒);实际值经 Settings.trigger_retry_backoff_sec 注入
DEFAULT_BACKOFF_SEC = 60


def is_valid_max_retries(value: int) -> bool:
    """max_retries 值域判定(0–5;0=不重试)。管理面 400 与 DDL CHECK 同源。"""
    return 0 <= value <= MAX_RETRIES_LIMIT


def schedule_retry(
    *,
    status: str,
    attempt: int,
    max_retries: int,
    now: dt.datetime,
    backoff_sec: int,
) -> dt.datetime | None:
    """执行落定后的重试判定:返回下次重试到期时刻(需重试)或 None(链终态)。

    - attempt=本次落定行的尝试序号(首跑=1);max_retries=允许的重试次数
      (不含首跑)。FAILED 且 attempt <= max_retries 即剩余次数>0。
    - 返回值非 None 时调用方落一行挂起重试(status=PENDING、attempt+1、
      retry_of_run_id 关联链首跑、next_retry_at=返回值);调度 tick 到期派发。
    - now 须带时区(与服务层同钟 Asia/Shanghai)。
    """
    if status == "FAILED" and attempt <= max_retries:
        return now + dt.timedelta(seconds=backoff_sec)
    return None
