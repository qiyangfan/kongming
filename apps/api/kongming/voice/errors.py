"""voice 域领域错误(router 统一映射状态码;message 即用户可见文案)。"""


class VoiceError(Exception):
    """voice 域错误基类。"""


class VoiceNotEnabled(VoiceError):
    """未配置实时语音模型:通话入口不出现,端点 503。"""


class UserCallConflict(VoiceError):
    """同一用户已有一通在途通话(409)。"""


class ConcurrencyFull(VoiceError):
    """部署级并发通话数已满(409 当前繁忙)。"""


class AdmissionUnavailable(VoiceError):
    """准入后端不可用:fail-closed 拒绝建立(503)。"""


class RateLimited(VoiceError):
    """单位时间建联次数超限(429)。"""


class UserDailyLimitExceeded(VoiceError):
    """用户 24 小时建联预算用尽(429)。"""


class DeploymentDailyLimitExceeded(VoiceError):
    """部署 24 小时建联预算用尽(429 当前繁忙)。"""


class SignalingFailed(VoiceError):
    """牵线失败:上游拒绝/应答非法/网络错误(502;原文不透传)。"""


class OfferInvalid(VoiceError):
    """连接请求不合法(超限或缺 v=0 头):客户端错误(400)。"""


class TicketInvalid(VoiceError):
    """通道票据缺失/过期/复用/非本人(通道关闭,不泄露通话是否存在)。"""


class CallNotFound(VoiceError):
    """通话不存在或已结束。"""
