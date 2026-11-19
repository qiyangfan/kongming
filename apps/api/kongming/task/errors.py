"""task 域用例异常(用例层与协作账本共引,避免循环 import)。

router 按类型映射 404/409/422;TaskInvalidTransition 的审计事件已随事务落库,
端点须先 commit 再 409。
"""


class TaskNotFound(Exception):
    """不存在/越权(404 同形)。"""


class TaskConflict(Exception):
    """状态/版本冲突(409)。"""


class TaskInvalidTransition(TaskConflict):
    """非法状态迁移(白名单拒绝;审计事件已落,端点需先 commit 再 409)。"""


class TopologyBotNotFound(Exception):
    """协作拓扑声明的 Bot 不存在/停用/创建者不可用(422 同族,非归属 404)。"""
