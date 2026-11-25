"""chat 领域异常与用户文案映射(用例/适配/suggestions 共用)。"""


def blocked_friendly(reason: str | None) -> str:
    """按拦截原因前缀映射用户文案;问答与执行计划共用。"""
    r = reason or ""
    if r.startswith("MEMORY_SENSITIVE"):
        # 显式记忆敏感内容拒落(独立文案,与通用安全检查区分)
        return "该内容含敏感信息(密码/密钥/证件等),不予记忆"
    if r.startswith("GUARDRAIL"):
        # 输入护栏拦截(对外不点名规则,统一安全检查文案)
        return "内容未通过安全检查,请调整后重试"
    if r.startswith("DATA_SCOPE"):
        return "该查询超出您的数据范围,暂不支持"
    if r.startswith("RESERVED_TABLE"):
        # 中性不责备——常见成因是模型越界而非用户过错
        return "该问题涉及的数据不允许查询,请调整问法或咨询管理员"
    return "该查询涉及非只读操作,已被拒绝"


class TenantForbidden(Exception):
    """未绑定目标租户(或租户不存在/禁用——同形不区分)。"""


class AttachmentNotFound(Exception):
    """附件不存在/越权/已消费——同形 404。"""


class SessionNotFound(Exception):
    """会话不存在或不属于当前用户+租户(同形不区分,防探测)。"""


class InvalidDatasource(Exception):
    """数据源不存在/禁用/不归属当前租户(同形不区分,防 key 探测;入口预检)。"""


class MessageNotFound(Exception):
    """消息不存在或不属于当前用户+租户(执行计划;同形不区分,防探测)。"""


class VoiceTurnInvalid(Exception):
    """语音轮关联字段不存在/已使用/非本人(400;不落消息、不回调 Agent 面)。"""


class TurnNotFound(Exception):
    """轮次不在途且本人名下无落库结果(不存在、他人轮次、已结束未落库同形 404)。"""


class ExplainNoSql(Exception):
    """消息无落库 SQL(user 问句/纯失败轮)——无计划可分析。"""


class InvalidTitle(Exception):
    """标题去空白后为空或超列上限(重命名;参数错误,映射 400)。"""
