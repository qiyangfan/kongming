"""worker 续读入口(task-kernel「派发与上下文受控传递」变更)。

task_context 是 worker 的**缺省内置只读工具**(授权交集外加:只读自身
任务上下文不含数据源访问,不构成提权);零参形态——task_id 绑定派发
载荷,LLM 无法指定他任务,跨任务读取结构性不可能。截断尾提示常量供
协作派发摘要与圆桌黑板在发生截断时附加,未截断不附加(行为零变化)。
"""

TASK_CONTEXT_TOOL_NAME = "task_context"

TASK_CONTEXT_TOOL_SPEC: dict[str, object] = {
    "name": TASK_CONTEXT_TOOL_NAME,
    "description": (
        "读取当前任务的目标全文与各步骤状态/结论摘要(只读,限当前任务;"
        "派发上下文摘要被截断时按需读取)"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

TASK_CONTEXT_HINT = (
    "注:以上摘要因体积上限被截断;需要完整任务目标或各步骤结论时,"
    "可调用 task_context 工具按需读取(仅限当前任务)。"
)
