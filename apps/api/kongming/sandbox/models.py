"""sandbox 领域模型(027,零框架,宪法 Article II)。

状态四终态与 sys_sandbox_run CHECK 同值域;SandboxOutcome 是运行器与
用例层之间的解耦形态(宿主侧事实:duration/exit_code 由运行器测定,
runner 进程内协议行不含这两项)。
"""

from dataclasses import dataclass, field
from typing import Literal

SandboxStatus = Literal["SUCCESS", "ERROR", "TIMEOUT", "RESOURCE_LIMIT"]

ALL_STATUSES: tuple[str, ...] = ("SUCCESS", "ERROR", "TIMEOUT", "RESOURCE_LIMIT")

# 与镜像内 runner 的协议行前缀(design D2);宿主只认最后一行
PROTOCOL_PREFIX = "__KM_RESULT__"


@dataclass(frozen=True)
class SandboxOutcome:
    """一次沙箱执行的宿主侧事实。images 为 base64 png(≤500KB/张,027 v1 仅末图)。"""

    status: SandboxStatus
    stdout: str = ""
    error: str | None = None
    images: list[str] = field(default_factory=list)
    image_note: str | None = None
    result_preview: str | None = None
    data_rows: int = 0
    data_cols: int = 0
    duration_ms: int = 0
    exit_code: int | None = None
