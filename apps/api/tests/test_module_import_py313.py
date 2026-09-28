# pyright: reportPrivateUsage=false
"""070 防回归:feedback 组合根在注解求值语义下可导入(3.13 事故固化)。

背景:容器(Python 3.13)import 时 kongming.feedback.router 在
_FeedbackSampleGate.__init__ 签名注解处 NameError——RagRepository 等只在
TYPE_CHECKING 块导入。测试 venv(3.14,PEP 649 惰性注解)把该 bug 完全掩盖,
pyright 亦因类型态可见而静默。本测试以 3.13 语义静态固化:模块源码中
TYPE_CHECKING 不得出现"运行时取用的名字"。

判据(有牙齿,不是摆设):解析模块 AST;凡是在函数签名默认值/注解、except
子句、装饰器、基类等"定义时即求值"位置出现的 Name,必须能在非
TYPE_CHECKING 的 import 语句中解析到。
"""

import ast
import builtins
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _runtime_names(tree: ast.Module) -> set[str]:
    """模块级(含 TYPE_CHECKING 外的)import 提供的运行时名字。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


def _type_checking_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom):
                    names.update(a.asname or a.name for a in sub.names)
    return names


def test_no_type_checking_only_names_in_eager_positions() -> None:
    """定义时求值位置(签名/except/基类/装饰器)禁现 TYPE_CHECKING 专有名。"""
    tc_only = _type_checking_names(
        ast.parse((_ROOT / "kongming" / "feedback" / "router.py").read_text())
    )
    assert tc_only == set(), (
        f"feedback/router.py 应为运行时导入(组合根真名);仍残留 TYPE_CHECKING 专有名: {tc_only}"
    )


def test_gate_class_annotations_resolve_at_runtime() -> None:
    """_FeedbackSampleGate.__init__ 签名注解全部可运行解析(3.13 语义)。"""
    import kongming.feedback.router as mod

    src = (_ROOT / "kongming" / "feedback" / "router.py").read_text()
    tree = ast.parse(src)
    runtime = _runtime_names(tree)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "_FeedbackSampleGate":
            for sub in ast.walk(node):
                if isinstance(sub, ast.arg) and sub.annotation is not None:
                    names.update(n.id for n in ast.walk(sub.annotation) if isinstance(n, ast.Name))
    missing = {n for n in names if n not in runtime and not hasattr(builtins, n)}
    assert not missing, f"签名注解名运行时可解析失败(3.13 会 NameError): {missing}"
    assert hasattr(mod, "_FeedbackSampleGate")


def test_except_clause_names_resolve_at_runtime() -> None:
    """except 子句取的名必须运行时可解析(EmbeddingError 503 路径)。"""
    import kongming.feedback.router as mod

    src = (_ROOT / "kongming" / "feedback" / "router.py").read_text()
    tree = ast.parse(src)
    runtime = _runtime_names(tree)
    caught = {
        n.id
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is not None
        for n in ast.walk(node.type)
        if isinstance(n, ast.Name)
    }
    missing = {n for n in caught if n not in runtime and not hasattr(builtins, n)}
    assert not missing, f"except 子句名运行时可解析失败: {missing}"
    assert "EmbeddingError" in caught, "应存在 EmbeddingError 捕获(070 503 路径)"
    # mod 的引用即本测试的存在意义:导入本身成功=3.13 定义时求值不炸
    assert hasattr(mod, "_FeedbackSampleGate")
