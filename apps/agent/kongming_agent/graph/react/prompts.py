"""ReAct 内核 prompt(019)。

**全新撰写资产**(非上游逐字移植——prompt 资产纪律只管 graph/prompts.py 的
上游逐字面);全角中文标点纪律沿用,改动走评审。
R1-L4:身份段/纪律段显式分段常量(REACT_SYSTEM 由常量组装;Bot 注入路径
零 split 漂移——REACT_SYSTEM 演进只改对应段落常量)。
079:纪律段按注入工具集派生(条款 + 工具名占位),默认全套件渲染结果与
历史 REACT_DISCIPLINE 逐字相同;写能力陈述随 write 元数据变化,不再静态断言。
"""

from collections.abc import Iterable, Sequence

# 默认身份段(Bot 模式替换此段)
REACT_IDENTITY_DEFAULT = "你是孔明数据助手的自主决策内核。"

# ── 工具名常量(与 toolcall 注册表对齐;派生纪律段的占位来源)──
T_RAG = "rag_retrieve"
T_VALIDATE = "validate_sql"
T_EXECUTE = "execute_sql"
T_WRITE = "create_system_announcement"

# 无工具纪律段(能力-契约一致:注入工具集为空时替换工具纪律——防模型按
# 工具纪律「文本伪调用」，产出「自言自语+伪代码」假应答被当作最终答案)
REACT_NO_TOOLS_DISCIPLINE = """你当前没有任何可用工具。不要尝试调用工具，不要输出工具调用计划、伪代码或代码块。

若用户问题需要查询或操作数据，如实说明「当前助手未配置数据工具，无法执行该查询」，不得编造数值或数据；其余问题直接以中文给出最终答案，内容不得包含编造的事实。"""


def _clause_intro() -> str:
    return (
        "你可以调用提供的工具来完成用户的请求；每次调用后你会收到工具返回的结果，"
        "据此决定继续调用工具还是直接给出最终答案。"
    )


def _clause_sql_query(*, has_rag: bool, has_validate: bool, has_execute: bool) -> str | None:
    """规则 1:数据查询类问题(按可用工具裁剪)。"""
    if not (has_rag or has_validate or has_execute):
        return None
    steps: list[str] = []
    if has_rag:
        steps.append(f"先用 {T_RAG} 检索相关知识（表结构与历史成功 SQL），再编写 SQL")
    else:
        steps.append("直接编写 SQL")
    if has_validate:
        steps.append(f"经 {T_VALIDATE} 校验通过后")
    if has_execute:
        steps.append(f"用 {T_EXECUTE} 执行")
    else:
        steps.append("输出 SQL 与说明（当前无执行工具，不得声称已执行）")
    body = "，".join(steps) if len(steps) > 1 else steps[0]
    return f"1. 数据查询类问题：{body}。"


def _clause_sql_style() -> str:
    return (
        "2. SQL 一律为 PostgreSQL 只读 SELECT；含 GROUP BY 或聚合函数的统计查询不加 LIMIT"
        "（用户明确要前 N 名时按统计键排序后 LIMIT）；明细查询默认 LIMIT 100 并给出确定性排序；"
        "排序键可能并列时追加主键等唯一列作第二排序键；平均值、比率等聚合小数值用 ROUND(…, 2)。"
    )


def _clause_write(*, write_tools: Sequence[str]) -> str:
    """规则 3:写能力陈述随注入写工具变化(079:删除全局静态断言)。

    全套件(恰为历史默认 create_system_announcement)走历史逐字文本——
    079-1.1 锚测试要求缺省场景零漂移;裁剪/扩展集才用自适应措辞。
    """
    if not write_tools:
        return (
            "3. 写操作：当前没有任何写工具。用户要求增删改数据时，"
            "只提供现状查询并在答复中如实说明未执行写操作。"
        )
    if list(write_tools) == [T_WRITE]:
        # 历史逐字(079-1.1 锚);勿改写此句
        return (
            "3. 写操作：除 create_system_announcement（系统公告）外没有任何写工具。"
            "用户要求增删改数据时，只提供现状查询并在答复中如实说明未执行写操作。"
        )
    names = "、".join(write_tools)
    return (
        f"3. 写操作：当前写工具仅有 {names}（以工具描述为准）。"
        "用户要求增删改数据时，除非用户明确要求且工具可用，否则只提供现状查询并如实说明。"
    )


def _clause_confirm() -> str:
    return (
        "4. 工具返回「需用户确认」时：停止一切后续工具调用，直接向用户说明有待确认事项及其内容，"
        "并明确告知：系统已在消息下方弹出确认卡片，请在卡片上点击「确认执行」或「拒绝」"
        "（不要让用户用文字回复确认）。"
    )


def _clause_retry() -> str:
    return "5. 工具被拒绝或失败时：根据原因换一种思路重试；同一失败不要原样重复。"


def _clause_budget(*, has_execute: bool) -> str:
    """规则 6:效率预算(点名工具的条款同口径裁剪)。"""
    if has_execute:
        return (
            f"6. 一般情况下工具调用不超过 4 轮；拿到表结构信息后立即编写 SQL 执行，"
            f"{T_EXECUTE} 成功返回结果后必须直接给出最终答案，不得再做「确认性」的额外调用。"
        )
    return "6. 一般情况下工具调用不超过 4 轮；拿到足够信息后必须直接给出最终答案，不得再做「确认性」的额外调用。"


def _clause_no_catalog_probe(*, has_rag: bool, has_execute: bool) -> str | None:
    if not has_rag:
        return None
    if has_execute:
        return (
            f"7. 禁止用 {T_EXECUTE} 探测 information_schema、pg_tables 等系统目录来摸表结构——"
            f"表结构只经 {T_RAG} 获得；{T_RAG} 已返回表结构时不得重复检索。"
        )
    return f"7. 表结构只经 {T_RAG} 获得；{T_RAG} 已返回表结构时不得重复检索。"


def _clause_no_dup() -> str:
    return "8. 同一工具同一参数的重复调用会被熔断拒绝；每条工具观察都是新信息，读完即用。"


def _clause_terminate() -> str:
    return (
        "终止纪律：拿到足够信息后，直接输出中文最终答案（不再调用任何工具）。"
        "最终答案中的数值结论必须以工具返回的数据为准，不得编造或外推。"
    )


def render_discipline(tool_names: Iterable[str]) -> str:
    """按注入工具集派生纪律段。默认全套件输出与历史 REACT_DISCIPLINE 逐字相同。"""
    names = set(tool_names)
    if not names:
        return REACT_NO_TOOLS_DISCIPLINE
    has_rag, has_validate = T_RAG in names, T_VALIDATE in names
    has_execute = T_EXECUTE in names
    write_tools = sorted(n for n in names if n == T_WRITE or n.startswith("mcp_write_"))
    return _render_renumbered(names, has_rag, has_validate, has_execute, write_tools)


# 历史全套件纪律段(锚测试 1.1 的逐字目标;由 render_discipline 全套件路径产出)
def _render_full_default() -> str:
    return (
        _clause_intro()
        + "\n\n工具使用纪律：\n"
        + (_clause_sql_query(has_rag=True, has_validate=True, has_execute=True) or "")
        + "\n"
        + _clause_sql_style()
        + "\n"
        + _clause_write(write_tools=(T_WRITE,))
        + "\n"
        + _clause_confirm()
        + "\n"
        + _clause_retry()
        + "\n\n效率纪律（硬约束，违反会触发熔断）：\n"
        + _clause_budget(has_execute=True)
        + "\n"
        + (_clause_no_catalog_probe(has_rag=True, has_execute=True) or "")
        + "\n"
        + _clause_no_dup()
        + "\n\n"
        + _clause_terminate()
    )


REACT_DISCIPLINE = _render_full_default()


def _render_renumbered(
    names: set[str],
    has_rag: bool,
    has_validate: bool,
    has_execute: bool,
    write_tools: list[str],
) -> str:
    # 全套件 → 历史逐字
    if {
        T_RAG,
        T_VALIDATE,
        T_EXECUTE,
        T_WRITE,
    } <= names and not any(n.startswith("mcp_write_") for n in names):
        return REACT_DISCIPLINE

    tool_rules: list[str] = []
    c = _clause_sql_query(has_rag=has_rag, has_validate=has_validate, has_execute=has_execute)
    if c:
        tool_rules.append(c.split(". ", 1)[1])
    tool_rules.append(_clause_sql_style().split(". ", 1)[1])
    tool_rules.append(_clause_write(write_tools=write_tools).split(". ", 1)[1])
    tool_rules.append(_clause_confirm().split(". ", 1)[1])
    tool_rules.append(_clause_retry().split(". ", 1)[1])
    numbered = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(tool_rules))

    eff: list[str] = []
    eff.append(_clause_budget(has_execute=has_execute).split(". ", 1)[1])
    probe = _clause_no_catalog_probe(has_rag=has_rag, has_execute=has_execute)
    if probe:
        eff.append(probe.split(". ", 1)[1])
    eff.append(_clause_no_dup().split(". ", 1)[1])
    # 效率段历史编号从 6 起;裁剪后从 tool_rules 末号+1 连续
    base = len(tool_rules) + 1
    eff_text = "\n".join(f"{base + i}. {r}" for i, r in enumerate(eff))

    return (
        _clause_intro()
        + "\n\n工具使用纪律：\n"
        + numbered
        + "\n\n效率纪律（硬约束，违反会触发熔断）：\n"
        + eff_text
        + "\n\n"
        + _clause_terminate()
    )


# ReAct 系统提示:身份段 + 纪律段显式组装(演进只改段落常量)
REACT_SYSTEM = REACT_IDENTITY_DEFAULT + REACT_DISCIPLINE


def build_react_system(
    bot_system_prompt: str, *, has_tools: bool, tool_names: Iterable[str] | None = None
) -> str:
    """Bot 模式系统提示(025 + 079):Bot 身份段替换默认身份;纪律段按注入
    工具集派生——空集=无工具纪律段;默认全套件=历史 REACT_DISCIPLINE 逐字;
    裁剪集=按可用工具重排条款(不再教模型调用未注入的工具)。"""
    bot = bot_system_prompt.strip()
    if not has_tools or tool_names is not None and not set(tool_names):
        discipline = REACT_NO_TOOLS_DISCIPLINE
    elif tool_names is None:
        discipline = REACT_DISCIPLINE  # 兼容旧调用(不传名单=历史全套件)
    else:
        discipline = render_discipline(tool_names)
    if not bot:
        return REACT_IDENTITY_DEFAULT + discipline
    return bot + "\n\n" + discipline


def initial_user_prompt(question: str, history: list[str], memory_hint: str = "") -> str:
    """首轮 user 消息:多轮历史(旧→新)+ 当前问题(与 8 节点图 history 语义同构);
    022:memory_hint 非空时追加「已知信息」段(控制面注入)。"""
    memory_part = f"{memory_hint.strip()}\n\n" if memory_hint.strip() else ""
    if history:
        history_text = "\n".join(f"第{i + 1}轮:{q}" for i, q in enumerate(history))
        return f"历史问题:\n{history_text}\n\n{memory_part}当前问题:{question}"
    return f"{memory_part}用户问题:{question}"
