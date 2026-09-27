"""079:ReAct 纪律段按注入工具集派生(全套件零漂移锚测试)。"""

from pathlib import Path

from kongming_agent.graph.react.prompts import (
    REACT_DISCIPLINE,
    REACT_NO_TOOLS_DISCIPLINE,
    T_EXECUTE,
    T_RAG,
    T_VALIDATE,
    T_WRITE,
    build_react_system,
    render_discipline,
)

FULL = {T_RAG, T_VALIDATE, T_EXECUTE, T_WRITE}


# 历史 REACT_DISCIPLINE 黄金夹具(079 引入前 git 版本;自比会掩盖漂移)
_LEGACY_DISCIPLINE = (Path(__file__).parent / "data_legacy_react_discipline.txt").read_text(
    encoding="utf-8"
)


def test_full_suite_matches_legacy_discipline() -> None:
    """1.1 锚:默认全套件渲染与历史 REACT_DISCIPLINE 逐字相同。"""
    assert render_discipline(FULL) == _LEGACY_DISCIPLINE
    assert REACT_DISCIPLINE == _LEGACY_DISCIPLINE


def test_empty_tools_matches_no_tools_discipline() -> None:
    assert render_discipline(set()) == REACT_NO_TOOLS_DISCIPLINE
    s = build_react_system("", has_tools=False, tool_names=[])
    assert REACT_NO_TOOLS_DISCIPLINE in s


def test_without_execute_no_execute_teaching() -> None:
    out = render_discipline({T_RAG, T_VALIDATE})
    assert T_EXECUTE not in out
    assert "execute_sql" not in out
    assert "不得声称已执行" in out
    assert T_RAG in out


def test_without_rag_no_retrieve_step() -> None:
    out = render_discipline({T_VALIDATE, T_EXECUTE})
    assert T_RAG not in out
    assert "先检索" not in out
    assert "先用 rag_retrieve" not in out
    # 余下条款仍在
    assert "只读 SELECT" in out
    assert "需用户确认" in out


def test_write_clause_follows_metadata() -> None:
    none_write = render_discipline({T_RAG, T_VALIDATE, T_EXECUTE})
    assert "当前没有任何写工具" in none_write
    assert T_WRITE not in none_write

    # 全套件:历史逐字句(含「除 create_system_announcement 外没有任何写工具」)
    with_write = render_discipline(FULL)
    assert T_WRITE in with_write
    assert "除 create_system_announcement（系统公告）外没有任何写工具" in with_write

    # 扩展写工具集:自适应措辞,点名全部写工具
    extra = render_discipline(FULL | {"mcp_write_notify"})
    assert "当前写工具仅有" in extra
    assert "mcp_write_notify" in extra
    assert "除 create_system_announcement" not in extra


def test_unknown_mcp_tools_not_imagined() -> None:
    out = render_discipline({T_RAG, "mcp_weather_query"})
    # 不臆断未注册工具语义:不出现其名的教学句
    assert "mcp_weather_query" not in out or "以工具描述为准" in out
    assert "先用 rag_retrieve" in out
