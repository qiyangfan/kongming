"""voice 领域模型:纯 dataclass,不依赖 Web 框架或数据库。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    """文字事件通道的一次性票据:绑定本人与通话,短时效。"""

    token: str
    user_id: str
    call_id: str
    ttl_sec: int = 60


@dataclass
class CallSession:
    """一通在途通话的运行时快照(进程内;单节点前提,多实例与在途轮登记一并外置)。

    轮数/打断/用量四个计数由状态机在通话中累加(唯一的计数承载处),收口审计
    据此取值——挂断与通道断开两条收口路径都能拿到真实值。
    """

    call_id: str
    user_id: str
    tenant_key: str
    started_at: float
    rounds: int = 0
    interrupts: int = 0
    audio_tokens_in: int = 0
    audio_tokens_out: int = 0


@dataclass(frozen=True)
class CallAudit:
    """通话结束的操作审计内容:不含语音内容与密钥。"""

    user_id: str
    tenant_key: str
    call_id: str
    duration_sec: int
    end_reason: str  # hung_up / max_duration / channel_closed / establish_failed
    rounds: int
    interrupts: int
    audio_tokens_in: int
    audio_tokens_out: int
    model: str
