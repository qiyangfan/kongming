"""trigger 领域错误:路由层按类型映射 HTTP 状态,服务层各处共抛。"""


class TriggerValidationError(Exception):
    """领域层 400(key/cron/目标形态)。"""


class TriggerNotFound(Exception):
    """不存在/跨租户同形。"""


class TriggerKeyTaken(Exception):
    """同租户 tg_key 冲突(409 语义)。"""


class TargetNotFound(Exception):
    """target_key 在当前租户对应目标域不存在(写边界闸,400 TARGET_NOT_FOUND)。"""


class WebhookRejected(Exception):
    """签名校验失败/禁用/不存在——同形(防探测)。"""
